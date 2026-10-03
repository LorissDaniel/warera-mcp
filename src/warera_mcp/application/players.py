"""Semantic player retrieval: public profiles and unambiguous username resolution.

Resolution never trusts a search hit as an identity. Search returns *candidate*
ids; each candidate profile is fetched (bounded, in parallel) and compared
case-insensitively against the requested username. Exactly one exact match is
required — zero yields ``NOT_FOUND``, more than one yields ``AMBIGUOUS_ENTITY``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from warera_mcp import errors as app_errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    bounded_try_reads,
    composite_observed_at,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.enums import PlayerField
from warera_mcp.domain.models import GetPlayerResult, PlayerProfile
from warera_mcp.domain.normalization import normalize_player_lite
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import as_sequence, coerce_str

PROFILE_PROCEDURE = "user.getUserLite"
SEARCH_USERS_PROCEDURE = "search.searchUsers"

#: Hard cap on candidate profiles fetched for one username lookup.
MAX_CANDIDATE_PROFILES = 5

DEFAULT_PLAYER_FIELDS: tuple[PlayerField, ...] = (
    PlayerField.PROFILE,
    PlayerField.LOCATION,
    PlayerField.LEVEL,
    PlayerField.SKILLS_SUMMARY,
    PlayerField.RANKINGS_SUMMARY,
)


@dataclass(frozen=True, slots=True)
class ResolvedPlayer:
    """A player identity resolved from an id or an exact username."""

    profile: PlayerProfile
    resolved_by: Literal["user_id", "username"]
    observed_at: datetime
    reads: tuple[UpstreamRead, ...] = ()


def require_player_identifier(user_id: str | None, username: str | None, operation: str) -> None:
    """Enforce the "exactly one identifier" contract."""
    if bool(user_id) == bool(username):
        raise app_errors.invalid_input(
            "provide exactly one of user_id or username", operation, field="identifier"
        )


def project_player_profile(
    profile: PlayerProfile, fields: list[PlayerField] | None
) -> PlayerProfile:
    """Reduce a profile to the requested field groups; identity is always kept."""
    requested = set(fields) if fields else set(DEFAULT_PLAYER_FIELDS)
    return PlayerProfile(
        id=profile.id,
        username=profile.username,
        level=profile.level if PlayerField.LEVEL in requested else None,
        country_id=profile.country_id if PlayerField.LOCATION in requested else None,
        region_id=profile.region_id if PlayerField.LOCATION in requested else None,
        skills=profile.skills if PlayerField.SKILLS_SUMMARY in requested else None,
        rankings=profile.rankings if PlayerField.RANKINGS_SUMMARY in requested else None,
    )


class PlayerResolver:
    """Resolves player identifiers to a public profile, or fails actionably."""

    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._max_candidates = max(1, min(MAX_CANDIDATE_PROFILES, runtime.settings.max_fanout))

    @property
    def max_candidates(self) -> int:
        """Documented cap on candidate profiles fetched per username lookup."""
        return self._max_candidates

    async def resolve(
        self,
        *,
        user_id: str | None,
        username: str | None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
        operation: str = "get_player",
    ) -> ResolvedPlayer:
        require_player_identifier(user_id, username, operation)
        if user_id:
            return await self._resolve_by_id(
                user_id, credentials=credentials, correlation_id=correlation_id, operation=operation
            )
        if username is None:  # pragma: no cover - guarded by require_player_identifier
            raise app_errors.invalid_input(
                "provide exactly one of user_id or username", operation, field="identifier"
            )
        return await self._resolve_by_username(
            username, credentials=credentials, correlation_id=correlation_id, operation=operation
        )

    async def resolve_id_or_username(
        self,
        *,
        user_id: str | None,
        username: str | None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
        operation: str,
    ) -> str:
        """Return just the resolved player id (used by composite company tools)."""
        resolved = await self.resolve(
            user_id=user_id,
            username=username,
            credentials=credentials,
            correlation_id=correlation_id,
            operation=operation,
        )
        return resolved.profile.id

    async def _resolve_by_id(
        self,
        user_id: str,
        *,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
        operation: str,
    ) -> ResolvedPlayer:
        read = await self._caller.read(
            operation,
            PROFILE_PROCEDURE,
            {"userId": user_id},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        profile, _ = normalize_player_lite(read.data, default_id=user_id)
        return ResolvedPlayer(
            profile=profile, resolved_by="user_id", observed_at=read.observed_at, reads=(read,)
        )

    async def _resolve_by_username(
        self,
        username: str,
        *,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
        operation: str,
    ) -> ResolvedPlayer:
        search_read = await self._caller.read(
            operation,
            SEARCH_USERS_PROCEDURE,
            {"searchText": username, "limit": self._max_candidates},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        candidate_ids = _candidate_ids(search_read.data, cap=self._max_candidates)
        if not candidate_ids:
            raise app_errors.not_found(
                f"no player matched username '{username}'", operation, reason="username_no_match"
            )

        outcomes = await bounded_try_reads(
            (
                self._caller.try_read(
                    operation,
                    PROFILE_PROCEDURE,
                    {"userId": candidate_id},
                    credentials=credentials,
                    correlation_id=correlation_id,
                )
                for candidate_id in candidate_ids
            ),
            limit=min(self._max_candidates, len(candidate_ids)),
        )

        target = username.casefold()
        matches: list[tuple[PlayerProfile, UpstreamRead]] = []
        reads: list[UpstreamRead] = [search_read]
        for outcome in outcomes:
            if not isinstance(outcome, UpstreamRead):
                continue
            reads.append(outcome)
            profile, _ = normalize_player_lite(outcome.data)
            if profile.username is not None and profile.username.casefold() == target:
                matches.append((profile, outcome))

        if not matches:
            raise app_errors.not_found(
                f"no player matched username '{username}' exactly",
                operation,
                reason="username_no_exact_match",
                candidate_count=len(candidate_ids),
            )
        if len(matches) > 1:
            raise app_errors.ambiguous_entity(
                f"username '{username}' matched {len(matches)} players",
                operation,
                candidates=tuple(profile.id for profile, _ in matches),
            )

        profile, _ = matches[0]
        return ResolvedPlayer(
            profile=profile,
            resolved_by="username",
            observed_at=composite_observed_at(reads),
            reads=tuple(reads),
        )


def _candidate_ids(payload: object, *, cap: int) -> list[str]:
    """Extract candidate user ids from a search payload (a bare list upstream)."""
    sequence = as_sequence(payload)
    if sequence is None:
        return []
    found: list[str] = []
    for item in sequence:
        candidate = coerce_str(item)
        if candidate is not None and candidate not in found:
            found.append(candidate)
        if len(found) >= cap:
            break
    return found


class PlayerService:
    """Semantic player operations."""

    def __init__(self, resolver: PlayerResolver) -> None:
        self._resolver = resolver

    async def get_player(
        self,
        *,
        user_id: str | None,
        username: str | None,
        fields: list[PlayerField] | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetPlayerResult:
        resolved = await self._resolver.resolve(
            user_id=user_id,
            username=username,
            credentials=credentials,
            correlation_id=correlation_id,
            operation="get_player",
        )
        warnings: list[str] = []
        if resolved.resolved_by == "username":
            warnings.append(
                "resolved by exact username match against up to "
                f"{self._resolver.max_candidates} search candidates"
            )
        return GetPlayerResult(
            observed_at=resolved.observed_at,
            warnings=warnings,
            player=project_player_profile(resolved.profile, fields),
            resolved_by=resolved.resolved_by,
        )


__all__ = [
    "DEFAULT_PLAYER_FIELDS",
    "MAX_CANDIDATE_PROFILES",
    "PROFILE_PROCEDURE",
    "SEARCH_USERS_PROCEDURE",
    "PlayerResolver",
    "PlayerService",
    "ResolvedPlayer",
    "project_player_profile",
    "require_player_identifier",
]
