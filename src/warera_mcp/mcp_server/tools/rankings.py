"""Battle-ranking MCP tool."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

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

GET_BATTLE_RANKING_DESCRIPTION = (
    "Get damage, points or money rankings for players, countries or military units. Provide "
    "exactly one battle_id, war_id or round_id. Continue with next_cursor and the same filters; "
    "item_count is the reported total when available. Names are resolved with bounded lookups. "
    "This is not a live combat feed; use get_military_unit_ranking for global MU rankings."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_battle_ranking",
        description=GET_BATTLE_RANKING_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_battle_ranking")
    async def get_battle_ranking(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        battle_id: OptionalIdentifier = None,
        war_id: OptionalIdentifier = None,
        round_id: OptionalIdentifier = None,
        cursor: OpaqueCursor = None,
        entity_type: Annotated[
            Literal["user", "country", "mu"],
            Field(default="user", description="Which entity type the ranking lists."),
        ] = "user",
        side: Annotated[
            Literal["attacker", "defender", "merged"],
            Field(default="merged", description="Which side of the battle to rank."),
        ] = "merged",
        metric: Annotated[
            Literal["damage", "points", "money"],
            Field(default="damage", description="Ranking metric."),
        ] = "damage",
        limit: SmallLimit = 5,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.rankings.get_battle_ranking(
            battle_id=battle_id,
            war_id=war_id,
            round_id=round_id,
            cursor=cursor,
            entity_type=entity_type,
            side=side,
            metric=metric,
            limit=limit,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        scope = result.battle_id or result.war_id or result.round_id
        summary = (
            f"Top {len(result.entries)} of {result.item_count} for scope {scope} "
            f"({result.entity_type}/{result.side}/{result.metric})"
        )
        return success_result(
            result,
            summary=summary,
            operation="get_battle_ranking",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["GET_BATTLE_RANKING_DESCRIPTION", "register"]
