"""Semantic battle operations: bounded battle search and a battle dossier.

``get_battle`` pairs the stable dossier with the volatile live snapshot. When
live data is unavailable the dossier is still returned with ``partial=True`` —
never discarded — and every volatile payload carries an observation timestamp.
"""

from __future__ import annotations

import asyncio

from warera_mcp import errors as app_errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    composite_observed_at,
)
from warera_mcp.application.world import (
    ALL_COUNTRIES_PROCEDURE,
    country_name_index,
    country_records,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.models import (
    BattleLiveStatus,
    GetBattleResult,
    SearchBattlesResult,
)
from warera_mcp.domain.normalization import (
    items_of,
    normalize_battle_detail,
    normalize_battle_summary,
    normalize_live_battle,
    page_info,
)
from warera_mcp.warera.client import UpstreamRead

BATTLES_PROCEDURE = "battle.getBattles"
BATTLE_BY_ID_PROCEDURE = "battle.getById"
LIVE_BATTLE_PROCEDURE = "battle.getLiveBattleData"

UNFILTERED_PAGE_WARNING = (
    "no country or active filter was supplied; this is the first upstream page and "
    "its ordering is not guaranteed"
)


class BattleService:
    """Semantic battle retrieval."""

    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    async def search_battles(
        self,
        *,
        country_id: str | None = None,
        is_active: bool | None = None,
        limit: int,
        cursor: str | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> SearchBattlesResult:
        operation = "search_battles"
        params: dict[str, object] = {"limit": limit}
        if country_id is not None:
            params["countryId"] = country_id
        if is_active is not None:
            params["isActive"] = is_active
        if cursor is not None:
            params["cursor"] = cursor

        read = await self._caller.read(
            operation,
            BATTLES_PROCEDURE,
            params,
            credentials=credentials,
            correlation_id=correlation_id,
        )
        warnings: list[str] = []
        reads: list[UpstreamRead] = [read]
        country_names: dict[str, str] | None = None
        countries_outcome = await self._caller.try_read(
            operation,
            ALL_COUNTRIES_PROCEDURE,
            {},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        if isinstance(countries_outcome, UpstreamRead):
            reads.append(countries_outcome)
            country_names = country_name_index(country_records(countries_outcome.data))
        else:
            warnings.append("country names were unavailable; country ids are returned")

        battles = [
            normalize_battle_summary(item.data, country_names=country_names)
            for item in items_of(read.data)
        ]
        if country_id is None and is_active is None:
            warnings.append(UNFILTERED_PAGE_WARNING)
        page = page_info(read.data)
        if page.has_more:
            warnings.append("more battles are available; pass the cursor to continue")

        return SearchBattlesResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            battles=battles,
            page=page,
        )

    async def get_battle(
        self,
        *,
        battle_id: str,
        include_live_status: bool = True,
        include_history: bool = False,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetBattleResult:
        operation = "get_battle"
        live_outcome: UpstreamRead | app_errors.AppError | None = None
        if include_live_status:
            detail_outcome, live_outcome = await asyncio.gather(
                self._caller.try_read(
                    operation,
                    BATTLE_BY_ID_PROCEDURE,
                    {"battleId": battle_id},
                    credentials=credentials,
                    correlation_id=correlation_id,
                ),
                self._caller.try_read(
                    operation,
                    LIVE_BATTLE_PROCEDURE,
                    {"battleId": battle_id},
                    credentials=credentials,
                    correlation_id=correlation_id,
                ),
            )
        else:
            detail_outcome = await self._caller.try_read(
                operation,
                BATTLE_BY_ID_PROCEDURE,
                {"battleId": battle_id},
                credentials=credentials,
                correlation_id=correlation_id,
            )

        if isinstance(detail_outcome, app_errors.AppError):
            raise detail_outcome

        reads: list[UpstreamRead] = [detail_outcome]
        country_names: dict[str, str] | None = None
        countries_outcome = await self._caller.try_read(
            operation,
            ALL_COUNTRIES_PROCEDURE,
            {},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        if isinstance(countries_outcome, UpstreamRead):
            reads.append(countries_outcome)
            country_names = country_name_index(country_records(countries_outcome.data))
        country_warning = (
            "country names were unavailable; country ids are returned"
            if not isinstance(countries_outcome, UpstreamRead)
            else None
        )

        detail, warnings = normalize_battle_detail(
            detail_outcome.data,
            include_history=include_history,
            default_id=battle_id,
            country_names=country_names,
        )
        if country_warning is not None:
            warnings.append(country_warning)
        live: BattleLiveStatus | None = None
        partial = False

        if include_live_status:
            if isinstance(live_outcome, UpstreamRead):
                live = normalize_live_battle(live_outcome.data)
                reads.append(live_outcome)
            else:
                partial = True
                warnings.append("live battle data was unavailable")

        return GetBattleResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            battle=detail,
            live_status=live,
            partial=partial,
        )


__all__ = [
    "BATTLES_PROCEDURE",
    "BATTLE_BY_ID_PROCEDURE",
    "LIVE_BATTLE_PROCEDURE",
    "UNFILTERED_PAGE_WARNING",
    "BattleService",
]
