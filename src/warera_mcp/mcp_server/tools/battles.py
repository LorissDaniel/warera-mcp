"""Battle-facing MCP tools."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    OpaqueCursor,
    RequiredIdentifier,
    SmallLimit,
)

SEARCH_BATTLES_DESCRIPTION = (
    "List battles, optionally filtered by country and active state. Without a filter this is the "
    "first upstream page, and its ordering is not guaranteed, so do not describe it as 'latest'. "
    "For a battle leaderboard use get_battle_ranking."
)

GET_BATTLE_DESCRIPTION = (
    "Get a battle's status: sides, current round and optionally a live snapshot. Live values "
    "are volatile snapshots with an explicit tick timestamp. Last-hits lists and equipment "
    "payloads are excluded by design."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="search_battles",
        description=SEARCH_BATTLES_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("search_battles")
    async def search_battles(
        ctx: ToolContext,
        country_id: Annotated[
            str | None, Field(default=None, max_length=64, description="Filter by country id.")
        ] = None,
        is_active: Annotated[
            bool | None, Field(default=None, description="Filter to active or finished battles.")
        ] = None,
        limit: SmallLimit = 5,
        cursor: OpaqueCursor = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.battles.search_battles(
            country_id=country_id,
            is_active=is_active,
            limit=limit,
            cursor=cursor,
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.battles)} battles returned",
            operation="search_battles",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_battle", description=GET_BATTLE_DESCRIPTION, annotations=READ_ONLY_ANNOTATIONS
    )
    @tool_errors("get_battle")
    async def get_battle(
        ctx: ToolContext,
        battle_id: RequiredIdentifier,
        include_live_status: Annotated[
            bool, Field(default=True, description="Also fetch the live round snapshot.")
        ] = True,
        include_history: Annotated[
            bool, Field(default=False, description="Include previous rounds when available.")
        ] = False,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.battles.get_battle(
            battle_id=battle_id,
            include_live_status=include_live_status,
            include_history=include_history,
            correlation_id=call_id(ctx),
        )
        state = "active" if result.battle.is_active else "not active"
        return success_result(
            result,
            summary=f"Battle {result.battle.id} is {state}",
            operation="get_battle",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["GET_BATTLE_DESCRIPTION", "SEARCH_BATTLES_DESCRIPTION", "register"]
