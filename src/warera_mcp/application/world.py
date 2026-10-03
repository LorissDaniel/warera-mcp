"""Semantic world operations: country overview, region lookup and current wars.

Country identity (id or exact name) is resolved once and reused for the
composite. ``wars_with`` is the country record's *current* relationship field —
not a war-history endpoint — and ``get_country_wars`` states that limitation
explicitly rather than implying treaty or historical detail.
"""

from __future__ import annotations

from collections.abc import Sequence

from warera_mcp import errors as app_errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    composite_observed_at,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.models import (
    BattleSummary,
    CountryFacts,
    CountryRef,
    GetCountryOverviewResult,
    GetCountryWarsResult,
    GetRegionResult,
    OpponentCountry,
    PageInfo,
    RegionSummary,
)
from warera_mcp.domain.normalization import (
    items_of,
    normalize_battle_summary,
    normalize_country,
    normalize_region_detail,
    normalize_region_summary,
    page_info,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import (
    Record,
    as_mapping,
    as_sequence,
    coerce_str,
    extract_ident,
    record_of,
)

ALL_COUNTRIES_PROCEDURE = "country.getAllCountries"
COUNTRY_BY_ID_PROCEDURE = "country.getCountryById"
REGIONS_OBJECT_PROCEDURE = "region.getRegionsObject"
REGION_BY_ID_PROCEDURE = "region.getById"
BATTLES_PROCEDURE = "battle.getBattles"


def _match_country_records(records: Sequence[Record], name: str) -> list[Record]:
    """Exact, case-insensitive country-name matching over raw records."""
    target = name.casefold()
    return [
        record
        for record in records
        if (raw := coerce_str(record.raw("name"))) is not None and raw.casefold() == target
    ]


def country_name_index(records: Sequence[Record]) -> dict[str, str]:
    index: dict[str, str] = {}
    for record in records:
        country_id = extract_ident(record.raw("_id")) or extract_ident(record.raw("id"))
        name = coerce_str(record.raw("name"))
        if country_id and name:
            index[country_id] = name
    return index


def _region_records(payload: object) -> list[Record]:
    """Normalize the ``region.getRegionsObject`` map into a list of records."""
    mapping = as_mapping(payload)
    if mapping is None:
        return []
    records: list[Record] = []
    for region_id, value in mapping.items():
        entry = as_mapping(value)
        if entry is None:
            continue
        merged = dict(entry)
        merged.setdefault("_id", region_id)
        records.append(Record(merged, path="$.regions"))
    return records


class WorldService:
    """Semantic country and region retrieval."""

    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    # -------------------------------------------------------------- resolution
    async def _all_countries(
        self,
        *,
        operation: str,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
    ) -> tuple[list[Record], UpstreamRead]:
        read = await self._caller.read(
            operation,
            ALL_COUNTRIES_PROCEDURE,
            {},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        return country_records(read.data), read

    async def resolve_country(
        self,
        *,
        country_id: str | None,
        country_name: str | None,
        operation: str,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> tuple[CountryFacts, list[UpstreamRead]]:
        """Resolve a country by id or exact name."""
        if bool(country_id) == bool(country_name):
            raise app_errors.invalid_input(
                "provide exactly one of country_id or country_name", operation, field="country"
            )
        if country_id:
            read = await self._caller.read(
                operation,
                COUNTRY_BY_ID_PROCEDURE,
                {"countryId": country_id},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            facts, _ = normalize_country(read.data)
            return facts, [read]

        if country_name is None:  # pragma: no cover - guarded by the XOR check above
            raise app_errors.invalid_input("country_name is required", operation, field="country")
        records, read = await self._all_countries(
            operation=operation, credentials=credentials, correlation_id=correlation_id
        )
        matches = _match_country_records(records, country_name)
        if not matches:
            raise app_errors.not_found(
                f"no country named '{country_name}'", operation, reason="country_no_match"
            )
        if len(matches) > 1:
            raise app_errors.ambiguous_entity(
                f"country name '{country_name}' matched {len(matches)} countries",
                operation,
                candidates=tuple(
                    ident
                    for record in matches
                    if (ident := extract_ident(record.raw("_id"))) is not None
                ),
            )
        facts, _ = normalize_country(matches[0].data)
        return facts, [read]

    # ------------------------------------------------------------------ tools
    async def get_country_overview(
        self,
        *,
        country_id: str | None,
        country_name: str | None,
        include_regions: bool,
        limit_regions: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetCountryOverviewResult:
        operation = "get_country_overview"
        facts, reads = await self.resolve_country(
            country_id=country_id,
            country_name=country_name,
            operation=operation,
            credentials=credentials,
            correlation_id=correlation_id,
        )
        warnings: list[str] = []

        regions: list[RegionSummary] | None = None
        regions_page: PageInfo | None = None
        if include_regions:
            region_read = await self._caller.read(
                operation,
                REGIONS_OBJECT_PROCEDURE,
                {},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            reads.append(region_read)
            matching = [
                record
                for record in _region_records(region_read.data)
                if _region_country_id(record) == facts.id
            ]
            page = matching[:limit_regions]
            regions = [normalize_region_summary(record.data) for record in page]
            regions_page = PageInfo(has_more=len(matching) > len(page))
            if not matching:
                warnings.append("no regions were listed for this country")

        return GetCountryOverviewResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            country=facts,
            regions=regions,
            regions_page=regions_page,
        )

    async def get_region(
        self,
        *,
        region_id: str | None,
        region_name: str | None,
        include_country: bool,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetRegionResult:
        operation = "get_region"
        if bool(region_id) == bool(region_name):
            raise app_errors.invalid_input(
                "provide exactly one of region_id or region_name", operation, field="region"
            )

        if region_id:
            read = await self._caller.read(
                operation,
                REGION_BY_ID_PROCEDURE,
                {"regionId": region_id},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            detail, warnings = normalize_region_detail(read.data)
            reads = [read]
        else:
            if region_name is None:  # pragma: no cover - guarded by the XOR check above
                raise app_errors.invalid_input("region_name is required", operation, field="region")
            read = await self._caller.read(
                operation,
                REGIONS_OBJECT_PROCEDURE,
                {},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            target = region_name.casefold()
            matches = [
                record
                for record in _region_records(read.data)
                if (raw := coerce_str(record.raw("name"))) is not None and raw.casefold() == target
            ]
            if not matches:
                raise app_errors.not_found(
                    f"no region named '{region_name}'", operation, reason="region_no_match"
                )
            if len(matches) > 1:
                raise app_errors.ambiguous_entity(
                    f"region name '{region_name}' matched {len(matches)} regions",
                    operation,
                    candidates=tuple(
                        ident for record in matches if (ident := _region_id(record)) is not None
                    ),
                )
            detail, warnings = normalize_region_detail(matches[0].data)
            reads = [read]

        country: CountryRef | None = None
        if include_country and detail.country_id is not None:
            try:
                country_read = await self._caller.read(
                    operation,
                    COUNTRY_BY_ID_PROCEDURE,
                    {"countryId": detail.country_id},
                    credentials=credentials,
                    correlation_id=correlation_id,
                )
            except app_errors.AppError:
                warnings.append("linked country could not be resolved")
            else:
                facts, _ = normalize_country(country_read.data)
                country = CountryRef(id=facts.id, name=facts.name, code=facts.code)
                reads.append(country_read)

        return GetRegionResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            region=detail,
            country=country,
        )

    async def get_country_wars(
        self,
        *,
        country_id: str | None,
        country_name: str | None,
        include_active_battles: bool,
        limit_battles: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetCountryWarsResult:
        operation = "get_country_wars"
        facts, reads = await self.resolve_country(
            country_id=country_id,
            country_name=country_name,
            operation=operation,
            credentials=credentials,
            correlation_id=correlation_id,
        )
        warnings = ["wars_with is the country's current relationship snapshot, not a war history"]

        names: dict[str, str] = {}
        if facts.wars_with:
            _, country_read = await self._all_countries(
                operation=operation, credentials=credentials, correlation_id=correlation_id
            )
            reads.append(country_read)
            names = country_name_index(country_records(country_read.data))

        opponents = [
            OpponentCountry(country_id=opponent_id, name=names.get(opponent_id))
            for opponent_id in facts.wars_with or []
        ]

        active_battles: list[BattleSummary] | None = None
        page = PageInfo()
        partial = False
        if include_active_battles:
            battle_outcome = await self._caller.try_read(
                operation,
                BATTLES_PROCEDURE,
                {"countryId": facts.id, "isActive": True, "limit": limit_battles},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            if isinstance(battle_outcome, UpstreamRead):
                reads.append(battle_outcome)
                active_battles = [
                    normalize_battle_summary(item.data) for item in items_of(battle_outcome.data)
                ]
                page = page_info(battle_outcome.data)
            else:
                partial = True
                active_battles = []
                warnings.append("active battles could not be retrieved")

        return GetCountryWarsResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            country=CountryRef(id=facts.id, name=facts.name),
            opponents=opponents,
            active_battles=active_battles,
            page=page,
            partial=partial,
        )


def country_records(payload: object) -> list[Record]:
    """Extract country records from a bare array or an ``{items: [...]}`` wrapper."""
    record = record_of(payload)
    items = record.objects("items")
    if items:
        return items
    sequence = as_sequence(payload)
    if sequence is None:
        return []
    return [
        Record(mapping, path="$.countries")
        for item in sequence
        if (mapping := as_mapping(item)) is not None
    ]


def _region_country_id(record: Record) -> str | None:
    return extract_ident(record.raw("country")) or extract_ident(record.raw("countryId"))


def _region_id(record: Record) -> str | None:
    return extract_ident(record.raw("_id")) or extract_ident(record.raw("id"))


__all__ = [
    "ALL_COUNTRIES_PROCEDURE",
    "BATTLES_PROCEDURE",
    "COUNTRY_BY_ID_PROCEDURE",
    "REGIONS_OBJECT_PROCEDURE",
    "REGION_BY_ID_PROCEDURE",
    "WorldService",
    "country_name_index",
    "country_records",
]
