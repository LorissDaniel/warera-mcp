"""Additional semantic MCP reads, with explicit visibility, units and pagination."""

from __future__ import annotations

from typing import Annotated

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
    OptionalIdentifier,
    PlayerContextInput,
    player_context_credentials,
)

Username = Annotated[
    str | None,
    Field(
        max_length=64,
        pattern=SAFE_TEXT_PATTERN,
        description="Exact in-game username; provide this or user_id, not both.",
    ),
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
        name="get_player_equipment",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get a player's CURRENT equipped items by user_id or exact username: slot, item "
            "ID/code,  "
            "reported skill values, state/max_state, quantity and acquisition date. Anonymous "
            "read;  "
            "this is not private inventory or all owned equipment. empty_slots lists explicit null "
            "slots; omitted slots are unknown. Skills retain API units, not automatically "
            "fractions.  "
            "Maps are bounded (20 slots, 64 skills). Use get_player for aggregate skill totals and "
            "get_round_hits(include_equipment=true) for equipment recorded at a historical hit."
        ),
    )
    @tool_errors("get_player_equipment")
    async def get_player_equipment(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        user_id: OptionalIdentifier = None,
        username: Username = None,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.equipment.get_equipment(
            user_id=user_id,
            username=username,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_player_equipment",
            f"{len(result.equipment)} equipped slots for {result.user_id}",
        )
