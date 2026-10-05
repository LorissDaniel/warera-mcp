"""The game's authenticated, ranked company-location recommendations."""

from __future__ import annotations

import asyncio

from warera_mcp import errors as app_errors
from warera_mcp.application.common import UpstreamCaller, composite_observed_at
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.game_configuration_normalization import finite_number
from warera_mcp.domain.item_catalog import WARERA_ITEM_CODES
from warera_mcp.domain.models import (
    ProductionBonus,
    RecommendedRegion,
    RecommendedRegionsResult,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import as_mapping, as_sequence, clean_name, extract_ident

RECOMMENDATIONS = "company.getRecommendedRegionIdsByItemCode"
BONUS_FIELDS = {
    "strategicBonus": "strategic",
    "depositBonus": "deposit",
    "ethicSpecializationBonus": "ethic_specialization",
    "ethicDepositBonus": "ethic_deposit",
}


class LocationService:
    def __init__(self, caller: UpstreamCaller) -> None:
        self._caller = caller

    async def get_recommended_regions(
        self,
        *,
        item_code: str,
        include_deposit: bool,
        limit: int,
        offset: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> RecommendedRegionsResult:
        operation = "get_recommended_regions"
        if item_code not in WARERA_ITEM_CODES:
            raise app_errors.invalid_input("unknown item_code; use get_item_catalog", operation)
        read = await self._caller.read(
            operation,
            RECOMMENDATIONS,
            {"itemCode": item_code, "includeDeposit": include_deposit},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        rows = as_sequence(read.data)
        if rows is None:
            raise app_errors.upstream_schema_changed(
                "recommendation list shape was not recognized", operation, procedure=RECOMMENDATIONS
            )
        warnings = [
            "coverage is the game's returned recommendations, not an exhaustive world ranking"
        ]
        regions: list[RecommendedRegion] = []
        partial = False
        seen: set[str] = set()
        for value in rows:
            row = as_mapping(value)
            region_id = extract_ident(row.get("regionId")) if row is not None else None
            bonus = finite_number(row.get("bonus")) if row is not None else None
            if row is None or region_id is None or bonus is None or region_id in seen:
                partial = True
                continue
            seen.add(region_id)
            components: dict[str, float] = {}
            for upstream, canonical in BONUS_FIELDS.items():
                number = finite_number(row.get(upstream))
                if number is None:
                    partial = True
                else:
                    components[canonical] = number / 100
            if not include_deposit and any(
                components.get(key) != 0 for key in ("deposit", "ethic_deposit")
            ):
                partial = True
                warnings.append("a region could not be verified as excluding deposit bonuses")
                continue
            tax = finite_number(row.get("taxPercent"))
            if tax is None:
                partial = True
            regions.append(
                RecommendedRegion(
                    region_id=region_id,
                    production_bonus=ProductionBonus(
                        total_fraction=bonus / 100, components=components
                    ),
                    tax_fraction=tax / 100 if tax is not None else None,
                )
            )
        if partial:
            warnings.append("some recommendation fields or rows were unavailable or invalid")
        if rows and not regions:
            raise app_errors.upstream_schema_changed(
                "recommendations contained no usable verified rows",
                operation,
                procedure=RECOMMENDATIONS,
            )
        # Keep the official ranking; no reconstructed formula or local deposit subtraction.
        page = regions[offset : offset + limit]
        reads = [read]
        if page:
            region_read, country_read = await asyncio.gather(
                self._caller.try_read(
                    operation, "region.getRegionsObject", correlation_id=correlation_id
                ),
                self._caller.try_read(
                    operation, "country.getAllCountries", correlation_id=correlation_id
                ),
            )
            region_map = (
                as_mapping(region_read.data) if isinstance(region_read, UpstreamRead) else None
            )
            countries = (
                as_sequence(country_read.data) if isinstance(country_read, UpstreamRead) else None
            )
            country_names = {
                ident: clean_name(row.get("name"))
                for value in countries or []
                if (row := as_mapping(value)) is not None
                and (ident := extract_ident(row)) is not None
            }
            for entry in page:
                row = (
                    as_mapping(region_map.get(entry.region_id)) if region_map is not None else None
                )
                if row is None:
                    partial = True
                    continue
                entry.region_name = clean_name(row.get("name"))
                entry.country_id = extract_ident(row.get("country"))
                entry.country_name = country_names.get(entry.country_id or "")
                if entry.region_name is None or entry.country_name is None:
                    partial = True
            for outcome in (region_read, country_read):
                if isinstance(outcome, UpstreamRead):
                    reads.append(outcome)
                else:
                    partial = True
            if partial:
                warnings.append("some region or country names may be unavailable")
        return RecommendedRegionsResult(
            observed_at=composite_observed_at(reads),
            item_code=item_code,
            include_deposit=include_deposit,
            regions=page,
            total_count=len(regions),
            has_more=offset + len(page) < len(regions),
            partial=partial,
            warnings=list(dict.fromkeys(warnings)),
        )
