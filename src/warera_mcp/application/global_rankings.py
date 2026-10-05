"""Additional cohesive read services, with reviewed scopes and bounded projections."""

from __future__ import annotations

from warera_mcp import errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    GlobalRankingEntry,
    GlobalRankingResult,
)
from warera_mcp.domain.extended_normalization import (
    object_rows,
    require_object,
)
from warera_mcp.domain.normalization import (
    profile_numeric_map,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.errors import WareraSchemaError


class GlobalRankingService:
    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    async def _read(
        self,
        operation: str,
        procedure: str,
        params: dict[str, object],
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
    ) -> UpstreamRead:
        return await self._caller.read(
            operation, procedure, params, credentials=credentials, correlation_id=correlation_id
        )

    async def get_global_ranking(
        self,
        *,
        ranking_type: str,
        offset: int,
        limit: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GlobalRankingResult:
        read = await self._read(
            "get_global_ranking",
            "ranking.getRanking",
            {"rankingType": ranking_type},
            credentials,
            correlation_id,
        )
        r = require_object(read.data)
        rows = object_rows(r.raw("items"))
        entity = next(
            (
                kind
                for kind in ("country", "user", "alliance", "mu")
                if kind in ranking_type.lower()
            ),
            None,
        )
        if entity is None:
            raise errors.invalid_input("unknown ranking type", "get_global_ranking")
        entries = []
        for row in rows[offset : offset + limit]:
            ident = row.ident(entity)
            if ident is None:
                raise WareraSchemaError("ranking entry has no entity ID")
            entries.append(
                GlobalRankingEntry(
                    entity_id=ident,
                    country_id=row.ident("country"),
                    military_unit_id=row.ident("mu"),
                    rank=row.integer("rank"),
                    value=row.num("value"),
                    tier=row.opt_str("tier"),
                )
            )
        return GlobalRankingResult(
            observed_at=read.observed_at,
            ranking_type=ranking_type,
            entity_type=entity,
            ranking_id=r.ident("_id"),
            is_global=r.boolean("isGlobal"),
            entries=entries,
            tier_values=profile_numeric_map(r.raw("tierValues")),
            snapshot_count=len(rows),
            offset=offset,
            limit=limit,
            has_more=offset + limit < len(rows),
        )
