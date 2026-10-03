"""Semantic market and work operations.

Three intents are kept distinct because they answer different questions:
``get_market_price`` returns one quoted global price, ``search_market`` returns the
visible order book (explicitly *not* a market average), and ``get_work_market``
combines a wage benchmark with a bounded set of job offers.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from warera_mcp import errors as app_errors
from warera_mcp.application.catalog import ItemCatalog
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    composite_observed_at,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.models import (
    GetWorkMarketResult,
    MarketPrice,
    MarketPricesResult,
    OrderBookResult,
    WageStats,
    WorkOffer,
)
from warera_mcp.domain.normalization import (
    compute_depth,
    items_of,
    normalize_order_book,
    normalize_prices,
    normalize_wage_stats,
    normalize_work_offer,
    page_info,
)
from warera_mcp.warera.client import UpstreamRead

TOP_ORDERS_PROCEDURE = "tradingOrder.getTopOrders"
WAGE_STATS_PROCEDURE = "workOffer.getWageStats"
WORK_OFFERS_PROCEDURE = "workOffer.getWorkOffersPaginated"

MARKET_SIDES = ("buy", "sell", "both")

ORDER_BOOK_LIMITATION = (
    "visible top orders only; this is not a full-market average or guaranteed liquidity"
)


def _effective_wage(offer: WorkOffer) -> float | None:
    """Net wage when known, otherwise gross; never fabricated."""
    return offer.wage_after_tax if offer.wage_after_tax is not None else offer.wage


class MarketService:
    """Semantic market and work retrieval."""

    def __init__(
        self, caller: UpstreamCaller, runtime: ServiceRuntime, catalog: ItemCatalog
    ) -> None:
        self._caller = caller
        self._runtime = runtime
        self._catalog = catalog

    async def get_market_price(
        self,
        *,
        item_code: str,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MarketPrice:
        operation = "get_market_price"
        read = await self._catalog.read_prices(
            credentials=credentials, correlation_id=correlation_id
        )
        prices = normalize_prices(read.data)
        price = prices.get(item_code)
        if price is None:
            raise app_errors.not_found(
                f"unknown item_code '{item_code}'", operation, reason="unknown_item_code"
            )
        freshness = max(0.0, (datetime.now(UTC) - read.observed_at).total_seconds())
        return MarketPrice(
            observed_at=read.observed_at,
            warnings=[],
            item_code=item_code,
            price=price,
            freshness_seconds=round(freshness, 1),
        )

    async def get_market_prices(
        self,
        *,
        limit: int | None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> MarketPricesResult:
        read = await self._catalog.read_prices(
            credentials=credentials, correlation_id=correlation_id
        )
        prices = normalize_prices(read.data)
        ordered = dict(sorted(prices.items(), key=lambda entry: (-entry[1], entry[0])))
        warnings: list[str] = []
        if limit is not None and len(ordered) > limit:
            warnings.append(f"price catalog truncated to {limit} items")
            ordered = dict(list(ordered.items())[:limit])
        freshness = max(0.0, (datetime.now(UTC) - read.observed_at).total_seconds())
        return MarketPricesResult(
            observed_at=read.observed_at,
            warnings=warnings,
            prices=ordered,
            freshness_seconds=round(freshness, 1),
        )

    async def search_market(
        self,
        *,
        item_code: str,
        side: str,
        max_orders: int,
        depth_quantity: float | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> OrderBookResult:
        operation = "search_market"
        warnings = await self._catalog.validate_item_code(
            item_code, operation=operation, credentials=credentials, correlation_id=correlation_id
        )
        read = await self._caller.read(
            operation,
            TOP_ORDERS_PROCEDURE,
            {"itemCode": item_code},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        include_buy = side in ("buy", "both")
        include_sell = side in ("sell", "both")
        buy, sell = normalize_order_book(
            read.data,
            max_orders=max_orders,
            include_buy=include_buy,
            include_sell=include_sell,
        )

        best_bid = buy[0].price if buy else None
        best_ask = sell[0].price if sell else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None

        depth = None
        if depth_quantity is not None:
            if side == "both":
                warnings.append(
                    "depth estimation requires a single side; set side to 'buy' or 'sell'"
                )
            else:
                levels = buy if side == "buy" else sell
                depth = compute_depth(levels, depth_quantity)
                if depth is None:
                    warnings.append("depth could not be estimated from the visible book")
        warnings.append(ORDER_BOOK_LIMITATION)

        return OrderBookResult(
            observed_at=read.observed_at,
            warnings=warnings,
            item_code=item_code,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            buy_orders=buy,
            sell_orders=sell,
            depth=depth,
        )

    async def get_work_market(
        self,
        *,
        item_code: str,
        limit: int,
        region_id: str | None = None,
        minimum_net_wage: float | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GetWorkMarketResult:
        operation = "get_work_market"
        warnings = await self._catalog.validate_item_code(
            item_code, operation=operation, credentials=credentials, correlation_id=correlation_id
        )

        wage_outcome, offers_outcome = await asyncio.gather(
            self._caller.try_read(
                operation,
                WAGE_STATS_PROCEDURE,
                {"itemCode": item_code},
                credentials=credentials,
                correlation_id=correlation_id,
            ),
            self._caller.try_read(
                operation,
                WORK_OFFERS_PROCEDURE,
                {"limit": limit},
                credentials=credentials,
                correlation_id=correlation_id,
            ),
        )

        reads: list[UpstreamRead] = []
        partial = False

        wage_stats: WageStats | None = None
        if isinstance(wage_outcome, UpstreamRead):
            wage_stats = normalize_wage_stats(wage_outcome.data)
            reads.append(wage_outcome)
        else:
            partial = True
            warnings.append("wage statistics were unavailable")

        offers: list[WorkOffer] = []
        if isinstance(offers_outcome, UpstreamRead):
            reads.append(offers_outcome)
            raw_offers = [normalize_work_offer(item.data) for item in items_of(offers_outcome.data)]
            filtered = [
                offer
                for offer in raw_offers
                if _matches_work_filters(
                    offer, region_id=region_id, minimum_net_wage=minimum_net_wage
                )
            ]
            if len(filtered) != len(raw_offers):
                warnings.append("filters were applied locally to the first offer page")
            offers = filtered[:limit]
            if page_info(offers_outcome.data).has_more:
                warnings.append(
                    "more work offers are available upstream; narrow filters or paginate"
                )
        else:
            partial = True
            warnings.append("work offers were unavailable")

        return GetWorkMarketResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            item_code=item_code,
            wage_stats=wage_stats,
            offers=offers,
            partial=partial,
        )


def _matches_work_filters(
    offer: WorkOffer,
    *,
    region_id: str | None,
    minimum_net_wage: float | None,
) -> bool:
    if region_id is not None and offer.region_id != region_id:
        return False
    if minimum_net_wage is not None:
        wage = _effective_wage(offer)
        if wage is None or wage < minimum_net_wage:
            return False
    return True


__all__ = [
    "MARKET_SIDES",
    "ORDER_BOOK_LIMITATION",
    "TOP_ORDERS_PROCEDURE",
    "WAGE_STATS_PROCEDURE",
    "WORK_OFFERS_PROCEDURE",
    "MarketService",
]
