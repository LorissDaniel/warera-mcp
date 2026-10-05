"""Player-facing MCP tools."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.domain.enums import PlayerField
from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import quoted, success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    SAFE_TEXT_PATTERN,
    OptionalIdentifier,
    PlayerContextInput,
    player_context_credentials,
)

GET_PLAYER_DESCRIPTION = (
    "Get public player current skill summary/components, rankings, MU, activity and statistics. "
    "Use user_id "
    "or one exact username. Full profile adds region/location, company/party links, "
    "equipped-slot IDs, missions and wealth_breakdown from stats.wealth. fields selects groups; "
    "statistics includes wealth. include_full_profile=false uses lite only. "
    "profile_source identifies the source; failed enrichment keeps lite with partial=true. "
    "Missing values are unknown. Public wealth.money can differ from inventory money; "
    "items is not per-material stock. Use get_player_resources for available balances/stock."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_player_resources",
        description=(
            "Get a player's available money and basic materials, market reservations and optional "
            "owner-order totals including quantities for sale. Requires JWT: API key cannot read "
            "these endpoints. This does not infer spendable balance or stock from public "
            "wealth stats."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_player_resources")
    async def get_player_resources(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        user_id: OptionalIdentifier = None,
        username: Annotated[
            str | None, Field(default=None, max_length=64, pattern=SAFE_TEXT_PATTERN)
        ] = None,
        item_codes: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=64, pattern=SAFE_TEXT_PATTERN)]]
            | None,
            Field(
                default=None,
                max_length=24,
                description="Basic item codes; omit for all stored codes.",
            ),
        ] = None,
        include_orders: bool = True,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.resources.get_player_resources(
            user_id=user_id,
            username=username,
            item_codes=item_codes,
            include_orders=include_orders,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"Resources for {quoted(result.player.username or result.player.id)}",
            operation="get_player_resources",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_player", description=GET_PLAYER_DESCRIPTION, annotations=READ_ONLY_ANNOTATIONS
    )
    @tool_errors("get_player")
    async def get_player(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        user_id: OptionalIdentifier = None,
        username: Annotated[
            str | None,
            Field(
                default=None,
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="Exact in-game username.",
            ),
        ] = None,
        include_full_profile: Annotated[
            bool, Field(description="Enrich with reviewed fields from the public full profile.")
        ] = True,
        fields: Annotated[
            list[PlayerField] | None,
            Field(
                default=None,
                max_length=10,
                description="Groups: profile (MU/company/party), location "
                "(country/region/location), "
                "level, skills_summary, rankings_summary, activity, "
                "statistics (stats/wealth_breakdown), missions, "
                "equipment. "
                "Omit for all; identity is always retained.",
            ),
        ] = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.players.get_player(
            user_id=user_id,
            username=username,
            fields=fields,
            include_full_profile=include_full_profile,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        label = result.player.username or result.player.id
        summary = f"Public profile for {quoted(label)}"
        if result.player.level is not None:
            summary += f" (level {result.player.level})"
        return success_result(
            result,
            summary=summary,
            operation="get_player",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["GET_PLAYER_DESCRIPTION", "register"]
