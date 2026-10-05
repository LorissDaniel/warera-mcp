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
from warera_mcp.warera.schemas import as_sequence, extract_ident

PROFILE_PROCEDURE = "user.getUserLite"
FULL_PROFILE_PROCEDURE = "user.getUserById"
SEARCH_USERS_PROCEDURE = "search.searchUsers"

#: Hard cap on candidate profiles fetched for one username lookup.
MAX_CANDIDATE_PROFILES = 10

DEFAULT_PLAYER_FIELDS: tuple[PlayerField, ...] = (
    PlayerField.PROFILE,
    PlayerField.LOCATION,
    PlayerField.LEVEL,
    PlayerField.SKILLS_SUMMARY,
    PlayerField.RANKINGS_SUMMARY,
    PlayerField.ACTIVITY,
    PlayerField.STATISTICS,
    PlayerField.MISSIONS,
    PlayerField.EQUIPMENT,
)


@dataclass(frozen=True, slots=True)
class ResolvedPlayer:
    """A player identity resolved from an id or an exact username."""

    profile: PlayerProfile
    resolved_by: Literal["user_id", "username"]
    observed_at: datetime
    reads: tuple[UpstreamRead, ...] = ()
    warnings: tuple[str, ...] = ()


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
        skill_details=profile.skill_details if PlayerField.SKILLS_SUMMARY in requested else None,
        ranking_details=profile.ranking_details
        if PlayerField.RANKINGS_SUMMARY in requested
        else None,
        military_unit_id=profile.military_unit_id if PlayerField.PROFILE in requested else None,
        military_rank=profile.military_rank if PlayerField.PROFILE in requested else None,
        is_active=profile.is_active if PlayerField.PROFILE in requested else None,
        created_at=profile.created_at if PlayerField.PROFILE in requested else None,
        leveling=profile.leveling if PlayerField.LEVEL in requested else None,
        stats=profile.stats if PlayerField.STATISTICS in requested else None,
        activity_dates=profile.activity_dates if PlayerField.ACTIVITY in requested else None,
        location_id=profile.location_id if PlayerField.LOCATION in requested else None,
        company_id=profile.company_id if PlayerField.PROFILE in requested else None,
        party_id=profile.party_id if PlayerField.PROFILE in requested else None,
        updated_at=profile.updated_at if PlayerField.PROFILE in requested else None,
        military_unit_max_level_rewarded=profile.military_unit_max_level_rewarded
        if PlayerField.PROFILE in requested
        else None,
        equipment_ids=profile.equipment_ids if PlayerField.EQUIPMENT in requested else None,
        mission_statistics=profile.mission_statistics
        if PlayerField.MISSIONS in requested
        else None,
        mission_claimed_at=profile.mission_claimed_at
        if PlayerField.MISSIONS in requested
        else None,
        finished_tours=profile.finished_tours if PlayerField.MISSIONS in requested else None,
        activity_date_lists=profile.activity_date_lists
        if PlayerField.ACTIVITY in requested
        else None,
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

    async def full_profile(
        self,
        user_id: str,
        *,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
    ) -> UpstreamRead | app_errors.AppError:
        return await self._caller.try_read(
            "get_player",
            FULL_PROFILE_PROCEDURE,
            {"userId": user_id},
            credentials=credentials,
            correlation_id=correlation_id,
        )

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
        failures: list[app_errors.AppError] = []
        for candidate_id, outcome in zip(candidate_ids, outcomes, strict=True):
            if isinstance(outcome, app_errors.AppError):
                failures.append(outcome)
                continue
            if isinstance(outcome, BaseException):
                raise outcome  # a bug or cancellation, never a "no match"
            reads.append(outcome)
            profile, _ = normalize_player_lite(outcome.data, default_id=candidate_id)
            if profile.username is not None and profile.username.casefold() == target:
                matches.append((profile, outcome))

        if not matches:
            if failures:
                # Some candidates could not be checked, so "no such player" would be
                # a false claim. Surface the real (retryable) failure instead.
                raise failures[0]
            saturated = len(candidate_ids) >= self._max_candidates
            if saturated:
                raise app_errors.not_found(
                    f"no exact match for username '{username}' among the top "
                    f"{len(candidate_ids)} search results; the name may be shadowed by "
                    "similar names. Use the player's user_id instead.",
                    operation,
                    reason="username_not_in_top_candidates",
                    candidate_count=len(candidate_ids),
                )
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
        notes: tuple[str, ...] = ()
        if failures:
            notes = (
                f"{len(failures)} candidate profile(s) could not be checked; this is the only "
                "exact match among those that were",
            )
        return ResolvedPlayer(
            profile=profile,
            resolved_by="username",
            observed_at=composite_observed_at(reads),
            reads=tuple(reads),
            warnings=notes,
        )


def _candidate_ids(payload: object, *, cap: int) -> list[str]:
    """Extract candidate user ids from a search payload (a bare list upstream)."""
    sequence = as_sequence(payload)
    if sequence is None:
        return []
    found: list[str] = []
    for item in sequence:
        candidate = extract_ident(item)
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
        include_full_profile: bool = True,
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
        warnings: list[str] = list(resolved.warnings)
        profile = resolved.profile
        reads = list(resolved.reads)
        source: Literal["lite", "full"] = "lite"
        partial = False
        requested = set(fields or DEFAULT_PLAYER_FIELDS)
        if include_full_profile and requested - {
            PlayerField.LEVEL,
            PlayerField.SKILLS_SUMMARY,
            PlayerField.RANKINGS_SUMMARY,
        }:
            full = await self._resolver.full_profile(
                profile.id,
                credentials=credentials,
                correlation_id=correlation_id,
            )
            if isinstance(full, UpstreamRead):
                enriched, notes = normalize_player_lite(full.data)
                if enriched.id != profile.id:
                    partial = True
                    warnings.append(
                        "full profile identity was missing or mismatched; lite retained"
                    )
                else:
                    reads.append(full)
                    warnings.extend(notes)
                    profile = PlayerProfile.model_validate(
                        {
                            **profile.model_dump(exclude_none=True),
                            **enriched.model_dump(exclude_none=True),
                        }
                    )
                    source = "full"
            else:
                partial = True
                warnings.append("full public profile was unavailable; lite profile retained")
        if resolved.resolved_by == "username":
            warnings.append(
                "resolved by exact username match against up to "
                f"{self._resolver.max_candidates} search candidates"
            )
        return GetPlayerResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            player=project_player_profile(profile, fields),
            profile_source=source,
            partial=partial,
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
