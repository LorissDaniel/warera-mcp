"""Semantic company operations: owned-company lists and single-company overviews.

Both tools compose several public reads. Fan-out is bounded by
``settings.max_fanout`` and by explicit concurrency limits, and failures in
optional enrichment (production bonus, region context) degrade to a
``partial=True`` result with warnings instead of discarding usable data.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from warera_mcp import errors as app_errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    bounded_try_reads,
    composite_observed_at,
)
from warera_mcp.application.players import PlayerResolver
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.models import (
    CompanySummary,
    GetCompanyOverviewResult,
    GetPlayerCompaniesResult,
    PlayerRef,
    RegionSummary,
)
from warera_mcp.domain.normalization import (
    normalize_company_detail,
    normalize_company_summary,
    normalize_production_bonus,
    normalize_region_summary,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import as_sequence, coerce_str, record_of

COMPANIES_BY_USER_PROCEDURE = "company.getCompanies"
COMPANY_DETAIL_PROCEDURE = "company.getById"
PRODUCTION_BONUS_PROCEDURE = "company.getProductionBonus"
REGION_BY_ID_PROCEDURE = "region.getById"

#: Concurrency caps for the two enrichment phases of an owner listing.
DETAIL_CONCURRENCY = 8
BONUS_CONCURRENCY = 8


def company_ids(payload: object) -> list[str]:
    """Extract company ids from either ``{items:[...]}`` or a bare array."""
    record = record_of(payload)
    ids = record.strings("items")
    if ids:
        return ids
    sequence = as_sequence(payload)
    if sequence is None:
        return []
    return [text for item in sequence if (text := coerce_str(item)) is not None]


class CompanyService:
    """Semantic company retrieval."""

    def __init__(
        self,
        caller: UpstreamCaller,
        runtime: ServiceRuntime,
        players: PlayerResolver,
    ) -> None:
        self._caller = caller
        self._runtime = runtime
        self._players = players

    async def get_player_companies(
        self,
        *,
        user_id: str | None,
        username: str | None,
        limit: int,
        offset: int,
        include_bonus: bool,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetPlayerCompaniesResult:
        operation = "get_player_companies"
        resolved = await self._players.resolve(
            user_id=user_id,
            username=username,
            credentials=credentials,
            correlation_id=correlation_id,
            operation=operation,
        )
        listing = await self._caller.read(
            operation,
            COMPANIES_BY_USER_PROCEDURE,
            {"userId": resolved.profile.id},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        all_ids = company_ids(listing.data)
        warnings: list[str] = []

        effective_limit = min(limit, self._runtime.max_fanout)
        if effective_limit < limit:
            warnings.append(f"limit was clamped to the configured fan-out cap ({effective_limit})")
        page_ids = all_ids[offset : offset + effective_limit]
        total_count = len(all_ids)
        has_more = offset + len(page_ids) < total_count

        detail_task = asyncio.ensure_future(
            bounded_try_reads(
                [
                    self._caller.try_read(
                        operation,
                        COMPANY_DETAIL_PROCEDURE,
                        {"companyId": company_id},
                        credentials=credentials,
                        correlation_id=correlation_id,
                    )
                    for company_id in page_ids
                ],
                limit=min(DETAIL_CONCURRENCY, max(1, len(page_ids))),
            )
        )
        bonus_task = asyncio.ensure_future(
            self._bonus_outcomes(
                page_ids,
                include_bonus=include_bonus,
                credentials=credentials,
                correlation_id=correlation_id,
                operation=operation,
            )
        )
        detail_outcomes = await detail_task
        bonus_outcomes = await bonus_task

        partial = False
        companies: list[CompanySummary] = []
        reads: list[UpstreamRead] = [listing]
        for index, outcome in enumerate(detail_outcomes):
            if isinstance(outcome, UpstreamRead):
                reads.append(outcome)
                summary = normalize_company_summary(outcome.data, default_id=page_ids[index])
                bonus = bonus_outcomes.get(page_ids[index])
                if bonus is not None:
                    summary = summary.model_copy(update={"production_bonus": bonus})
                companies.append(summary)
            else:
                partial = True
                warnings.append(f"company {page_ids[index]} details were unavailable")

        if include_bonus and not bonus_outcomes and page_ids:
            partial = True
            warnings.append("production bonuses were requested but unavailable for this page")

        return GetPlayerCompaniesResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            player=PlayerRef(id=resolved.profile.id, username=resolved.profile.username),
            companies=companies,
            total_count=total_count,
            has_more=has_more,
            partial=partial,
        )

    async def _bonus_outcomes(
        self,
        page_ids: Sequence[str],
        *,
        include_bonus: bool,
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
        operation: str,
    ) -> dict[str, float]:
        """Fetch production bonuses concurrently, returning total fractions."""
        if not include_bonus or not page_ids:
            return {}
        outcomes = await bounded_try_reads(
            (
                self._caller.try_read(
                    operation,
                    PRODUCTION_BONUS_PROCEDURE,
                    {"companyId": company_id},
                    credentials=credentials,
                    correlation_id=correlation_id,
                )
                for company_id in page_ids
            ),
            limit=min(BONUS_CONCURRENCY, max(1, len(page_ids))),
        )
        totals: dict[str, float] = {}
        for company_id, outcome in zip(page_ids, outcomes, strict=True):
            if isinstance(outcome, UpstreamRead):
                bonus, _ = normalize_production_bonus(outcome.data)
                if bonus.total_fraction is not None:
                    totals[company_id] = bonus.total_fraction
        return totals

    async def get_company_overview(
        self,
        *,
        company_id: str,
        include_region_context: bool = False,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetCompanyOverviewResult:
        operation = "get_company_overview"
        detail_outcome, bonus_outcome = await asyncio.gather(
            self._caller.try_read(
                operation,
                COMPANY_DETAIL_PROCEDURE,
                {"companyId": company_id},
                credentials=credentials,
                correlation_id=correlation_id,
            ),
            self._caller.try_read(
                operation,
                PRODUCTION_BONUS_PROCEDURE,
                {"companyId": company_id},
                credentials=credentials,
                correlation_id=correlation_id,
            ),
        )

        if isinstance(detail_outcome, app_errors.AppError):
            raise detail_outcome

        detail, warnings = normalize_company_detail(detail_outcome.data, default_id=company_id)
        reads: list[UpstreamRead] = [detail_outcome]

        production_bonus = None
        if isinstance(bonus_outcome, UpstreamRead):
            production_bonus, bonus_warnings = normalize_production_bonus(bonus_outcome.data)
            warnings.extend(bonus_warnings)
            reads.append(bonus_outcome)
        else:
            warnings.append("production bonus was unavailable")

        recipe = None
        if detail.item_code is not None:
            recipe = self._runtime.recipes.lookup(detail.item_code)

        region: RegionSummary | None = None
        if include_region_context and detail.region_id is not None:
            region_outcome = await self._caller.try_read(
                operation,
                REGION_BY_ID_PROCEDURE,
                {"regionId": detail.region_id},
                credentials=credentials,
                correlation_id=correlation_id,
            )
            if isinstance(region_outcome, UpstreamRead):
                region = normalize_region_summary(region_outcome.data)
                reads.append(region_outcome)
            else:
                warnings.append("region context was unavailable")

        return GetCompanyOverviewResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            company=detail,
            production_bonus=production_bonus,
            recipe=recipe,
            region=region,
        )


__all__ = [
    "BONUS_CONCURRENCY",
    "COMPANIES_BY_USER_PROCEDURE",
    "COMPANY_DETAIL_PROCEDURE",
    "DETAIL_CONCURRENCY",
    "PRODUCTION_BONUS_PROCEDURE",
    "REGION_BY_ID_PROCEDURE",
    "CompanyService",
    "company_ids",
]
