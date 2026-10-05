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
    SAFE_TEXT_PATTERN,
    MediumLimit,
    OpaqueCursor,
    OptionalIdentifier,
    PlayerContextInput,
    player_context_credentials,
)

TransactionType = Literal[
    "applicationFee",
    "trading",
    "itemMarket",
    "wage",
    "donation",
    "articleTip",
    "openCase",
    "craftItem",
    "dismantleItem",
    "battleLoot",
    "countryMoneyTransfer",
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
        name="search_transactions",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Requires request-scoped API_KEY, not JWT. Search transaction pages with documented "
            "user/MU/country/party/item/type filters, applied upstream; omission leaves the API "
            "scope  "
            "unfiltered. Follow page.next_cursor with identical filters. Returns reported money, "
            "quantity, item attributes and buyer/seller entity links. Preserve buyer/seller "
            "labels:  "
            "a donation's buyer is not automatically a goods buyer. Money is not a unit price or "
            "signed cash-flow balance; do not infer those without transaction semantics. Never "
            "cached."
        ),
    )
    @tool_errors("search_transactions")
    async def search_transactions(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        user_id: OptionalIdentifier = None,
        military_unit_id: OptionalIdentifier = None,
        country_id: OptionalIdentifier = None,
        party_id: OptionalIdentifier = None,
        item_code: Annotated[
            str | None,
            Field(
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="Canonical item code filter, not an item instance ID.",
            ),
        ] = None,
        transaction_types: Annotated[
            list[TransactionType] | None,
            Field(
                min_length=1,
                max_length=11,
                description="Optional transaction type subset; duplicates are removed.",
            ),
        ] = None,
        cursor: OpaqueCursor = None,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.transactions.search_transactions(
            user_id=user_id,
            military_unit_id=military_unit_id,
            country_id=country_id,
            party_id=party_id,
            item_code=item_code,
            transaction_types=transaction_types,
            cursor=cursor,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx, result, "search_transactions", f"{len(result.transactions)} reported transactions"
        )
