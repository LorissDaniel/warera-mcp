"""Additional cohesive read services, with reviewed scopes and bounded projections."""

from __future__ import annotations

from collections.abc import Sequence

from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    TransactionsResult,
)
from warera_mcp.domain.extended_normalization import (
    object_rows,
    require_object,
    transaction,
)
from warera_mcp.domain.normalization import (
    page_info,
)
from warera_mcp.warera.client import UpstreamRead


class TransactionService:
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

    async def search_transactions(
        self,
        *,
        limit: int,
        user_id: str | None = None,
        military_unit_id: str | None = None,
        country_id: str | None = None,
        party_id: str | None = None,
        item_code: str | None = None,
        transaction_types: Sequence[str] | None = None,
        cursor: str | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> TransactionsResult:
        params: dict[str, object] = {"limit": limit}
        for key, value in (
            ("userId", user_id),
            ("muId", military_unit_id),
            ("countryId", country_id),
            ("partyId", party_id),
            ("itemCode", item_code),
            ("cursor", cursor),
        ):
            if value is not None:
                params[key] = value
        if transaction_types is not None:
            params["transactionType"] = list(dict.fromkeys(transaction_types))
        read = await self._read(
            "search_transactions",
            "transaction.getPaginatedTransactions",
            params,
            credentials,
            correlation_id,
        )
        r = require_object(read.data)
        rows = object_rows(r.raw("items"))
        truncated = len(rows) > limit
        return TransactionsResult(
            observed_at=read.observed_at,
            transactions=[transaction(v) for v in rows[:limit]],
            page=page_info(read.data),
            partial=truncated,
            warnings=["upstream transaction page exceeded limit; rows omitted"]
            if truncated
            else [],
        )
