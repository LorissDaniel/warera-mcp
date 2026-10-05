"""Read-only military-unit discovery, rosters, upgrades and global rankings."""

from __future__ import annotations

import math
from typing import Literal

from warera_mcp import errors as app_errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    bounded_try_reads,
    composite_observed_at,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.military_unit_normalization import (
    UNTRUSTED_MU_WARNING,
    identifier_list,
    normalize_military_unit,
    normalize_upgrade,
)
from warera_mcp.domain.models import (
    GetMilitaryUnitResult,
    MilitaryUnit,
    MilitaryUnitInvestment,
    MilitaryUnitInvestmentsResult,
    MilitaryUnitMember,
    MilitaryUnitMembersResult,
    MilitaryUnitRankingEntry,
    MilitaryUnitRankingResult,
    MilitaryUnitUpgrade,
    MilitaryUnitUpgradesResult,
    SearchMilitaryUnitsResult,
)
from warera_mcp.domain.normalization import page_info
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.errors import WareraSchemaError
from warera_mcp.warera.schemas import (
    as_mapping,
    as_sequence,
    coerce_float,
    extract_ident,
    record_of,
)

MU_BY_ID_PROCEDURE = "mu.getById"
MU_PAGE_PROCEDURE = "mu.getManyPaginated"
MU_RANKING_PROCEDURE = "ranking.getRanking"
MU_UPGRADE_PROCEDURE = "upgrade.getUpgradeByTypeAndEntity"
MilitaryUnitRankingType = Literal[
    "muWeeklyDamages",
    "muDamages",
    "muTerrain",
    "muWealth",
    "muBounty",
    "muReputation",
]
MilitaryUnitUpgradeType = Literal["headquarters", "dormitories"]


def _unit(payload: object, operation: str) -> tuple[MilitaryUnit, list[str]]:
    try:
        return normalize_military_unit(payload)
    except WareraSchemaError:
        raise app_errors.upstream_schema_changed(
            "WarEra returned an invalid military unit",
            operation,
        ) from None


def _page(payload: object, operation: str) -> list[object]:
    data = as_mapping(payload)
    items = as_sequence(data.get("items")) if data is not None else None
    if items is None:
        raise app_errors.upstream_schema_changed("WarEra returned an invalid page", operation)
    return list(items)


class MilitaryUnitService:
    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    async def search_military_units(
        self,
        *,
        search: str | None = None,
        member_id: str | None = None,
        owner_id: str | None = None,
        limit: int = 10,
        cursor: str | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> SearchMilitaryUnitsResult:
        operation = "search_military_units"
        params: dict[str, object] = {"limit": limit}
        for key, value in (
            ("search", search),
            ("memberId", member_id),
            ("userId", owner_id),
            ("cursor", cursor),
        ):
            if value is not None:
                params[key] = value
        read = await self._caller.read(
            operation,
            MU_PAGE_PROCEDURE,
            params,
            credentials=credentials,
            correlation_id=correlation_id,
        )
        items = _page(read.data, operation)
        if len(items) > limit:
            raise app_errors.upstream_schema_changed(
                "WarEra returned an oversized MU page", operation
            )
        units: list[MilitaryUnit] = []
        warnings = [UNTRUSTED_MU_WARNING]
        for item in items:
            unit, problems = _unit(item, operation)
            units.append(unit)
            warnings.extend(problems)
        page = page_info(read.data)
        if page.has_more:
            warnings.append(
                "more military units are available; pass next_cursor with the same filters"
            )
        return SearchMilitaryUnitsResult(
            observed_at=read.observed_at,
            warnings=list(dict.fromkeys(warnings)),
            military_units=units,
            page=page,
        )

    async def _detail(
        self,
        mu_id: str,
        operation: str,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
    ) -> tuple[UpstreamRead, MilitaryUnit, list[str]]:
        read = await self._caller.read(
            operation,
            MU_BY_ID_PROCEDURE,
            {"muId": mu_id},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        if read.data is None:
            raise app_errors.not_found(
                "WarEra has no military unit matching this identifier", operation
            )
        unit, warnings = _unit(read.data, operation)
        if unit.id != mu_id:
            raise app_errors.upstream_schema_changed(
                "WarEra returned a different MU identifier", operation
            )
        return read, unit, [UNTRUSTED_MU_WARNING, *warnings]

    async def get_military_unit(
        self,
        *,
        military_unit_id: str,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetMilitaryUnitResult:
        read, unit, warnings = await self._detail(
            military_unit_id,
            "get_military_unit",
            credentials,
            correlation_id,
        )
        return GetMilitaryUnitResult(
            observed_at=read.observed_at,
            warnings=warnings,
            military_unit=unit,
        )

    async def get_military_unit_members(
        self,
        *,
        military_unit_id: str,
        include_non_members: bool = False,
        offset: int = 0,
        limit: int = 10,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MilitaryUnitMembersResult:
        operation = "get_military_unit_members"
        read, unit, warnings = await self._detail(
            military_unit_id,
            operation,
            credentials,
            correlation_id,
        )
        record = record_of(read.data)
        members = identifier_list(record, "members")
        if members is None:
            raise app_errors.upstream_schema_changed("WarEra returned no MU member list", operation)
        roles = record.child("roles")
        managers = identifier_list(roles, "managers") if roles else None
        commanders = identifier_list(roles, "commanders") if roles else None
        targets = list(dict.fromkeys(members))
        if include_non_members:
            targets = list(
                dict.fromkeys(
                    [
                        *members,
                        *([unit.owner_id] if unit.owner_id else []),
                        *(managers or []),
                        *(commanders or []),
                    ]
                )
            )
        return MilitaryUnitMembersResult(
            observed_at=read.observed_at,
            warnings=warnings,
            military_unit_id=unit.id,
            members=[
                MilitaryUnitMember(
                    user_id=ident,
                    is_member=ident in members,
                    is_owner=ident == unit.owner_id if unit.owner_id is not None else None,
                    is_manager=ident in managers if managers is not None else None,
                    is_commander=ident in commanders if commanders is not None else None,
                )
                for ident in targets[offset : offset + limit]
            ],
            total_count=len(targets),
            offset=offset,
            limit=limit,
            has_more=offset + limit < len(targets),
        )

    async def get_military_unit_investments(
        self,
        *,
        military_unit_id: str,
        offset: int = 0,
        limit: int = 10,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MilitaryUnitInvestmentsResult:
        operation = "get_military_unit_investments"
        read, unit, warnings = await self._detail(
            military_unit_id,
            operation,
            credentials,
            correlation_id,
        )
        mapping = as_mapping(record_of(read.data).raw("investedMoneyByUsers"))
        if mapping is None:
            if record_of(read.data).has("investedMoneyByUsers"):
                raise app_errors.upstream_schema_changed(
                    "WarEra returned an invalid MU investment map",
                    operation,
                )
            return MilitaryUnitInvestmentsResult(
                observed_at=read.observed_at,
                warnings=[*warnings, "investment data was not returned; absence is not zero"],
                military_unit_id=unit.id,
                available=False,
                offset=offset,
                limit=limit,
                has_more=False,
            )
        investments: list[MilitaryUnitInvestment] = []
        for ident, raw in list(mapping.items())[offset : offset + limit]:
            user_id = extract_ident(ident)
            money = coerce_float(raw)
            if user_id is None or money is None or not math.isfinite(money) or money < 0:
                raise app_errors.upstream_schema_changed(
                    "WarEra returned invalid MU investments", operation
                )
            investments.append(MilitaryUnitInvestment(user_id=user_id, invested_money=money))
        return MilitaryUnitInvestmentsResult(
            observed_at=read.observed_at,
            warnings=warnings,
            military_unit_id=unit.id,
            available=True,
            investments=investments,
            total_count=len(mapping),
            offset=offset,
            limit=limit,
            has_more=offset + limit < len(mapping),
        )

    async def get_military_unit_ranking(
        self,
        *,
        ranking_type: MilitaryUnitRankingType = "muWeeklyDamages",
        offset: int = 0,
        limit: int = 10,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MilitaryUnitRankingResult:
        operation = "get_military_unit_ranking"
        read = await self._caller.read(
            operation,
            MU_RANKING_PROCEDURE,
            {"rankingType": ranking_type},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        items = _page(read.data, operation)
        entries: list[MilitaryUnitRankingEntry] = []
        for item in items[offset : offset + limit]:
            row = record_of(item)
            ident = row.ident("mu")
            if ident is None:
                raise app_errors.upstream_schema_changed(
                    "WarEra returned an invalid MU ranking row", operation
                )
            entries.append(
                MilitaryUnitRankingEntry(
                    military_unit_id=ident,
                    rank=row.integer("rank"),
                    value=row.num("value"),
                    tier=row.opt_str("tier"),
                )
            )
        reads = [read]
        names = await military_unit_names(
            self._caller,
            self._runtime,
            [entry.military_unit_id for entry in entries],
            operation=operation,
            credentials=credentials,
            correlation_id=correlation_id,
            reads=reads,
        )
        warnings = [UNTRUSTED_MU_WARNING]
        if any(entry.military_unit_id not in names for entry in entries):
            warnings.append("some military unit names could not be resolved; ids are returned")
        return MilitaryUnitRankingResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            ranking_type=ranking_type,
            entries=[
                entry.model_copy(update={"name": names.get(entry.military_unit_id)})
                for entry in entries
            ],
            total_count=len(items),
            offset=offset,
            limit=limit,
            has_more=offset + limit < len(items),
        )

    async def get_military_unit_upgrades(
        self,
        *,
        military_unit_id: str,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MilitaryUnitUpgradesResult:
        operation = "get_military_unit_upgrades"
        detail, _, warnings = await self._detail(
            military_unit_id,
            operation,
            credentials,
            correlation_id,
        )
        types = ("headquarters", "dormitories")
        outcomes = await bounded_try_reads(
            (
                self._caller.try_read(
                    operation,
                    MU_UPGRADE_PROCEDURE,
                    {"muId": military_unit_id, "upgradeType": kind},
                    credentials=credentials,
                    correlation_id=correlation_id,
                )
                for kind in types
            ),
            limit=self._runtime.max_fanout,
        )
        upgrades: list[MilitaryUnitUpgrade] = []
        reads = [detail]
        partial = False
        for kind, outcome in zip(types, outcomes, strict=True):
            if isinstance(outcome, UpstreamRead):
                reads.append(outcome)
                if outcome.data is None:
                    warnings.append(f"no {kind} upgrade was returned by WarEra")
                    continue
                try:
                    upgrades.append(
                        normalize_upgrade(
                            outcome.data,
                            mu_id=military_unit_id,
                            upgrade_type=kind,
                        )
                    )
                    continue
                except WareraSchemaError:
                    pass
            partial = True
            warnings.append(f"{kind} upgrade detail was unavailable or invalid")
        return MilitaryUnitUpgradesResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            military_unit_id=military_unit_id,
            upgrades=upgrades,
            partial=partial,
        )


async def military_unit_names(
    caller: UpstreamCaller,
    runtime: ServiceRuntime,
    identifiers: list[str],
    *,
    operation: str,
    credentials: PlayerRequestContext | None,
    correlation_id: str | None,
    reads: list[UpstreamRead],
) -> dict[str, str]:
    """Best-effort name enrichment within the shared fan-out and five-name budget."""
    targets = list(dict.fromkeys(identifiers))[: min(5, runtime.max_fanout)]
    outcomes = await bounded_try_reads(
        (
            caller.try_read(
                operation,
                MU_BY_ID_PROCEDURE,
                {"muId": ident},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            for ident in targets
        ),
        limit=runtime.max_fanout,
    )
    names: dict[str, str] = {}
    for ident, outcome in zip(targets, outcomes, strict=True):
        if not isinstance(outcome, UpstreamRead):
            continue
        record = record_of(outcome.data)
        if record.ident("_id") == ident and (name := record.name("name")) is not None:
            reads.append(outcome)
            names[ident] = name
    return names
