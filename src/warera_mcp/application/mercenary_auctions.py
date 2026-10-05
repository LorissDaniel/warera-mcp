"""Additional cohesive read services, with reviewed scopes and bounded projections."""

from __future__ import annotations

from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    MercenaryAuctionsResult,
)
from warera_mcp.domain.extended_normalization import (
    mercenary_auction,
    object_rows,
    require_object,
)
from warera_mcp.domain.normalization import (
    page_info,
)
from warera_mcp.warera.client import UpstreamRead


class MercenaryAuctionService:
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

    async def search_auctions(
        self,
        *,
        limit: int,
        country_id: str | None = None,
        battle_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        include_bids: bool = False,
        bid_limit: int = 10,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MercenaryAuctionsResult:
        params: dict[str, object] = {"limit": limit}
        for key, value in (
            ("countryId", country_id),
            ("battleId", battle_id),
            ("status", status),
            ("cursor", cursor),
        ):
            if value is not None:
                params[key] = value
        read = await self._read(
            "search_mercenary_auctions",
            "mercenaryContractAuction.getPaginatedAuctions",
            params,
            credentials,
            correlation_id,
        )
        r = require_object(read.data)
        rows = object_rows(r.raw("items"))
        auctions = [mercenary_auction(v, include_bids, bid_limit) for v in rows[:limit]]
        partial = len(rows) > limit or any(v.bids_truncated for v in auctions)
        return MercenaryAuctionsResult(
            observed_at=read.observed_at,
            warnings=["auction/bid data truncated; do not infer full coverage"] if partial else [],
            auctions=auctions,
            page=page_info(read.data),
            bids_included=include_bids,
            partial=partial,
        )
