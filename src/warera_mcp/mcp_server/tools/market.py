"""Market and work-market MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.domain.item_catalog import WARERA_ITEM_CODES
from warera_mcp.domain.models import ItemCatalogResult
from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    SAFE_TEXT_PATTERN,
    PlayerContextInput,
    player_context_credentials,
)

GET_ITEM_CATALOG_DESCRIPTION = (
    "Get the complete local catalog of canonical WarEra item codes. This is not a translated "
    "alias list, does not query upstream, and does not include prices or production recipes."
)

GET_MARKET_PRICES_DESCRIPTION = (
    "Get global quoted prices for multiple materials in ONE call. Prefer this tool whenever "
    "two or more item prices are needed; pass item_codes for a subset, or omit it for the "
    "complete catalog. Prices share one snapshot and are sorted by descending price. "
    "Missing quotes and optional limit truncation are explicit. This is not an order book "
    "or executable buy/sell price; use search_market for bids, asks and quantities."
)

GET_MARKET_PRICE_DESCRIPTION = (
    "Get the global quoted price for exactly one item. When multiple materials are needed, "
    "use get_market_prices with item_codes in ONE call rather than calling this tool per item. "
    "This is not an order book or an executable buy/sell price."
)

SEARCH_MARKET_DESCRIPTION = (
    "Inspect the visible buy/sell order book for one item, optionally estimating depth for one "
    "side. These are the top visible orders only: not guaranteed liquidity and never a "
    "full-market average. Order owners are deliberately omitted."
)

GET_WORK_MARKET_DESCRIPTION = (
    "Get the wage benchmark for one item together with a small set of matching work offers. "
    "Offers are filtered locally on the first upstream page. It reports wage facts only and does "
    "not assess eligibility or suitability."
)

ItemCode = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=SAFE_TEXT_PATTERN,
        description="WarEra item code, e.g. 'iron' or 'bread'.",
    ),
]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_market_price",
        description=GET_MARKET_PRICE_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_market_price")
    async def get_market_price(
        ctx: ToolContext, player_context: PlayerContextInput, item_code: ItemCode
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.market.get_market_price(
            item_code=item_code,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{result.item_code} global price is {result.price}",
            operation="get_market_price",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_item_catalog",
        description=GET_ITEM_CATALOG_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_item_catalog")
    async def get_item_catalog(
        ctx: ToolContext, player_context: PlayerContextInput
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = ItemCatalogResult(
            observed_at=datetime.now(UTC),
            item_codes=list(WARERA_ITEM_CODES),
            catalog_version="manual-v1",
        )
        return success_result(
            result,
            summary=f"{len(result.item_codes)} canonical WarEra item codes",
            operation="get_item_catalog",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_market_prices",
        description=GET_MARKET_PRICES_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_market_prices")
    async def get_market_prices(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        item_codes: Annotated[
            list[ItemCode] | None,
            Field(
                default=None,
                min_length=1,
                max_length=64,
                description="Item codes to return together; omit for all quoted items.",
            ),
        ] = None,
        limit: Annotated[
            int | None,
            Field(
                default=None,
                ge=1,
                le=5000,
                description="Optional maximum rows after filtering; omit for all requested prices.",
            ),
        ] = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.market.get_market_prices(
            limit=limit,
            item_codes=item_codes,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        summary = f"{len(result.prices)} global item prices"
        if result.prices:
            top = next(iter(result.prices.items()))
            summary += f"; highest quoted price: {top[0]}={top[1]}"
        if result.missing_item_codes:
            summary += f"; {len(result.missing_item_codes)} requested quotes unavailable"
        if result.truncated:
            summary += "; truncated by limit"
        return success_result(
            result,
            summary=summary,
            operation="get_market_prices",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="search_market",
        description=SEARCH_MARKET_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("search_market")
    async def search_market(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        item_code: ItemCode,
        side: Annotated[
            Literal["buy", "sell", "both"],
            Field(default="both", description="Book side to return."),
        ] = "both",
        max_orders: Annotated[
            int, Field(ge=1, le=10, default=5, description="Maximum price levels per side (1-10).")
        ] = 5,
        depth_quantity: Annotated[
            float | None,
            Field(
                default=None,
                gt=0,
                le=1_000_000,
                description="Estimate visible depth for this quantity on a single side.",
            ),
        ] = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.market.search_market(
            item_code=item_code,
            side=side,
            max_orders=max_orders,
            depth_quantity=depth_quantity,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        summary = f"{result.item_code} book"
        if result.best_bid is not None:
            summary += f": best bid {result.best_bid}"
        if result.best_ask is not None:
            summary += f", best ask {result.best_ask}"
        if result.spread is not None:
            summary += f" (spread {round(result.spread, 4)})"
        return success_result(
            result,
            summary=summary,
            operation="search_market",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_work_market",
        description=GET_WORK_MARKET_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_work_market")
    async def get_work_market(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        item_code: ItemCode,
        limit: Annotated[
            int, Field(ge=1, le=10, default=5, description="Maximum work offers to return (1-10).")
        ] = 5,
        region_id: Annotated[
            str | None,
            Field(
                default=None,
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="Filter offers by region id.",
            ),
        ] = None,
        minimum_net_wage: Annotated[
            float | None,
            Field(default=None, gt=0, le=1_000_000, description="Minimum acceptable wage."),
        ] = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.market.get_work_market(
            item_code=item_code,
            limit=limit,
            region_id=region_id,
            minimum_net_wage=minimum_net_wage,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        summary = f"{result.item_code}: {len(result.offers)} work offers"
        if result.wage_stats is not None and result.wage_stats.average is not None:
            summary += f" (average wage {result.wage_stats.average})"
        return success_result(
            result,
            summary=summary,
            operation="get_work_market",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = [
    "GET_ITEM_CATALOG_DESCRIPTION",
    "GET_MARKET_PRICES_DESCRIPTION",
    "GET_MARKET_PRICE_DESCRIPTION",
    "GET_WORK_MARKET_DESCRIPTION",
    "SEARCH_MARKET_DESCRIPTION",
    "register",
]
