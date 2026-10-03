"""Item-code validation against the verified global price catalog.

``itemTrading.getPrices`` returns the authoritative ``item_code -> price`` map.
The catalog service reuses it (cached) to reject unknown item codes before any
market/work read, so a typo becomes an actionable ``INVALID_INPUT``/``NOT_FOUND``
instead of an opaque upstream failure. When the catalog itself is unavailable the
validation degrades to a warning rather than blocking the request.
"""

from __future__ import annotations

from warera_mcp import errors as app_errors
from warera_mcp.application.common import UpstreamCaller
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.normalization import normalize_prices
from warera_mcp.warera.client import UpstreamRead

PRICES_PROCEDURE = "itemTrading.getPrices"


class ItemCatalog:
    """Validates item codes against the live, cached price snapshot."""

    def __init__(self, caller: UpstreamCaller) -> None:
        self._caller = caller

    async def read_prices(
        self,
        *,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> UpstreamRead:
        return await self._caller.read(
            "get_market_price",
            PRICES_PROCEDURE,
            {},
            credentials=credentials,
            correlation_id=correlation_id,
        )

    async def known_item_codes(
        self,
        *,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> frozenset[str] | None:
        """Return the known item codes, or ``None`` when the catalog is unavailable."""
        try:
            read = await self.read_prices(credentials=credentials, correlation_id=correlation_id)
        except app_errors.AppError:
            return None
        codes = normalize_prices(read.data)
        return frozenset(codes) if codes else None

    async def validate_item_code(
        self,
        item_code: str,
        *,
        operation: str,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> list[str]:
        """Return warnings; raise ``NOT_FOUND`` when the code is definitively unknown."""
        codes = await self.known_item_codes(credentials=credentials, correlation_id=correlation_id)
        if codes is None:
            return ["item_code could not be validated against the live item catalog"]
        if item_code not in codes:
            raise app_errors.not_found(
                f"unknown item_code '{item_code}'", operation, reason="unknown_item_code"
            )
        return []


__all__ = ["PRICES_PROCEDURE", "ItemCatalog"]
