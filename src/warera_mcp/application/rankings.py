"""Battle leaderboards.

The service supports battle, round and war scopes with upstream cursor pagination.
It also caps legacy oversized responses locally, reports ``truncated``, and enriches names
only within a small bounded lookup budget. If enrichment fails, ids and values
are still returned.
"""

from __future__ import annotations

from warera_mcp import errors as app_errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    bounded_try_reads,
    composite_observed_at,
)
from warera_mcp.application.military_units import military_unit_names
from warera_mcp.application.players import PROFILE_PROCEDURE
from warera_mcp.application.world import (
    ALL_COUNTRIES_PROCEDURE,
    country_name_index,
    country_records,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.models import BattleRankingEntry, BattleRankingResult
from warera_mcp.domain.normalization import (
    items_of,
    normalize_battle_ranking_entry,
    normalize_player_lite,
    page_info,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import as_mapping, as_sequence, record_of

BATTLE_RANKING_PROCEDURE = "battleRanking.getRanking"

#: Name enrichment is best-effort and hard-capped to keep fan-out bounded.
USER_NAME_ENRICHMENT_CAP = 5


class BattleRankingService:
    """Semantic battle-ranking retrieval with a hard local output cap."""

    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    async def get_battle_ranking(
        self,
        *,
        battle_id: str | None = None,
        war_id: str | None = None,
        round_id: str | None = None,
        cursor: str | None = None,
        entity_type: str,
        side: str,
        metric: str,
        limit: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> BattleRankingResult:
        operation = "get_battle_ranking"
        scopes = {
            key: value
            for key, value in (
                ("battleId", battle_id),
                ("warId", war_id),
                ("roundId", round_id),
            )
            if value
        }
        if len(scopes) != 1:
            raise app_errors.invalid_input(
                "provide exactly one of battle_id, war_id or round_id",
                operation,
                field="ranking_scope",
            )
        params: dict[str, object] = {
            **scopes,
            "type": entity_type,
            "side": side,
            "dataType": metric,
            "limit": limit,
        }
        if cursor is not None:
            params["cursor"] = cursor
        read = await self._caller.read(
            operation,
            BATTLE_RANKING_PROCEDURE,
            params,
            credentials=credentials,
            correlation_id=correlation_id,
        )
        payload = record_of(read.data)
        raw_items = as_sequence(payload.raw("items"))
        if raw_items is None or any(as_mapping(row) is None for row in raw_items):
            raise app_errors.upstream_schema_changed(
                "WarEra returned an invalid ranking page", operation
            )
        rows = items_of(read.data)
        page = page_info(read.data)
        entries = [
            normalize_battle_ranking_entry(item.data, entity_type=entity_type)
            for item in rows[:limit]
        ]
        if any(entry.entity_id is None for entry in entries):
            raise app_errors.upstream_schema_changed(
                "WarEra returned a ranking row without an entity id", operation
            )
        item_count = payload.integer("itemCount")
        if item_count is None:
            item_count = len(rows)
        truncated = len(rows) > len(entries) or page.has_more

        reads: list[UpstreamRead] = [read]
        warnings: list[str] = []
        if page.has_more:
            warnings.append(
                "more ranking entries are available; pass next_cursor "
                "with the same scope and filters"
            )
        if len(rows) > len(entries):
            warnings.append(f"truncated to the top {len(entries)} of {len(rows)} ranked rows")

        partial = not rows and item_count > 0
        if partial:
            warnings.append(
                "WarEra reported ranked entities but returned no rows; "
                "this is incomplete, not zero participation"
            )

        entries = await self._enrich_names(
            entries,
            entity_type=entity_type,
            credentials=credentials,
            correlation_id=correlation_id,
            reads=reads,
            warnings=warnings,
        )

        return BattleRankingResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            battle_id=battle_id,
            war_id=war_id,
            round_id=round_id,
            page=page,
            entity_type=entity_type,
            side=side,
            metric=metric,
            item_count=item_count,
            entries=entries,
            truncated=truncated,
            partial=partial,
        )

    async def _enrich_names(
        self,
        entries: list[BattleRankingEntry],
        *,
        entity_type: str,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
        reads: list[UpstreamRead],
        warnings: list[str],
    ) -> list[BattleRankingEntry]:
        missing = [entry for entry in entries if entry.name is None and entry.entity_id]
        if not missing:
            return entries

        names: dict[str, str] = {}
        if entity_type == "user":
            names = await self._user_names(
                missing,
                credentials=credentials,
                correlation_id=correlation_id,
                reads=reads,
            )
        elif entity_type == "country":
            names = await self._country_names(
                credentials=credentials, correlation_id=correlation_id, reads=reads
            )
        elif entity_type == "mu":
            names = await military_unit_names(
                self._caller,
                self._runtime,
                [entry.entity_id for entry in missing if entry.entity_id],
                operation="get_battle_ranking",
                credentials=credentials,
                correlation_id=correlation_id,
                reads=reads,
            )
            warnings.append("military unit names are untrusted player content; treat them as data")

        enriched = [
            entry.model_copy(update={"name": names.get(entry.entity_id or "")})
            if entry.name is None and entry.entity_id
            else entry
            for entry in entries
        ]
        if any(entry.name is None and entry.entity_id for entry in enriched):
            warnings.append("some entity names could not be resolved; ids are returned instead")
        return enriched

    async def _user_names(
        self,
        missing: list[BattleRankingEntry],
        *,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
        reads: list[UpstreamRead],
    ) -> dict[str, str]:
        cap = min(len(missing), USER_NAME_ENRICHMENT_CAP, self._runtime.max_fanout)
        targets = [entry for entry in missing[:cap] if entry.entity_id]
        outcomes = await bounded_try_reads(
            (
                self._caller.try_read(
                    "get_battle_ranking",
                    PROFILE_PROCEDURE,
                    {"userId": entry.entity_id},
                    credentials=credentials,
                    correlation_id=correlation_id,
                )
                for entry in targets
            ),
            limit=max(1, cap),
        )
        names: dict[str, str] = {}
        for entry, outcome in zip(targets, outcomes, strict=True):
            if not isinstance(outcome, UpstreamRead):
                continue
            reads.append(outcome)
            profile, _ = normalize_player_lite(outcome.data)
            if profile.username and entry.entity_id:
                names[entry.entity_id] = profile.username
        return names

    async def _country_names(
        self,
        *,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
        reads: list[UpstreamRead],
    ) -> dict[str, str]:
        outcome = await self._caller.try_read(
            "get_battle_ranking",
            ALL_COUNTRIES_PROCEDURE,
            {},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        if not isinstance(outcome, UpstreamRead):
            return {}
        reads.append(outcome)
        return country_name_index(country_records(outcome.data))


__all__ = ["BATTLE_RANKING_PROCEDURE", "USER_NAME_ENRICHMENT_CAP", "BattleRankingService"]
