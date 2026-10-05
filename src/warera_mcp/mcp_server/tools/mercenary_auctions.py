"""Additional semantic MCP reads, with explicit visibility, units and pagination."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.domain.models import ToolResult
from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    OpaqueCursor,
    OptionalIdentifier,
    PlayerContextInput,
    SmallLimit,
    player_context_credentials,
)

AuctionStatus = Literal[
    "active", "won", "expiredNoBids", "expiredBattle", "expiredRound", "cancelled", "terminated"
]


def respond(ctx: ToolContext, result: ToolResult, operation: str, summary: str) -> CallToolResult:
    return success_result(
        result,
        operation=operation,
        summary=summary,
        max_bytes=runtime_of(ctx).settings.max_output_bytes,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="search_mercenary_auctions",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Search mercenary contract auctions using upstream country, battle and status filters. "
            "Omit status to use the API default; do not assume it includes every historical "
            "status.  "
            "Follow page.next_cursor with identical filters. Reports budget, minimum damage, "
            "current  "
            "perK/payout, winning MU/user and expiry. include_bids adds a bounded bid list; "
            "bid_count  "
            "covers the returned auction record and bids_truncated flags omissions. Preserve API "
            "duration/rate units; bids and current payouts are not guaranteed completed rewards."
        ),
    )
    @tool_errors("search_mercenary_auctions")
    async def search_mercenary_auctions(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        country_id: OptionalIdentifier = None,
        battle_id: OptionalIdentifier = None,
        status: AuctionStatus | None = None,
        cursor: OpaqueCursor = None,
        limit: SmallLimit = 5,
        include_bids: bool = False,
        bid_limit: Annotated[
            int,
            Field(
                ge=1, le=20, description="Maximum bids per returned auction; no bid cursor exists."
            ),
        ] = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.mercenary_auctions.search_auctions(
            country_id=country_id,
            battle_id=battle_id,
            status=status,
            cursor=cursor,
            limit=limit,
            include_bids=include_bids,
            bid_limit=bid_limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx, result, "search_mercenary_auctions", f"{len(result.auctions)} mercenary auctions"
        )
