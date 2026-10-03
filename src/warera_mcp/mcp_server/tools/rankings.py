"""Battle-ranking MCP tool."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import READ_ONLY_ANNOTATIONS, RequiredIdentifier, SmallLimit

GET_BATTLE_RANKING_DESCRIPTION = (
    "Get a battle leaderboard by damage or points for players, countries, or military units on one "
    "side. The upstream ranking is unbounded, so results are capped locally and `truncated` says "
    "whether more rows exist. This is a battle ranking, not the deferred global wealth ranking."
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
        battle_id: RequiredIdentifier,
        entity_type: Annotated[
            Literal["user", "country", "mu"],
            Field(default="user", description="Which entity type the ranking lists."),
        ] = "user",
        side: Annotated[
            Literal["attacker", "defender", "merged"],
            Field(default="merged", description="Which side of the battle to rank."),
        ] = "merged",
        metric: Annotated[
            Literal["damage", "points"],
            Field(default="damage", description="Ranking metric."),
        ] = "damage",
        limit: SmallLimit = 5,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.rankings.get_battle_ranking(
            battle_id=battle_id,
            entity_type=entity_type,
            side=side,
            metric=metric,
            limit=limit,
            correlation_id=call_id(ctx),
        )
        summary = (
            f"Top {len(result.entries)} of {result.item_count} for battle {result.battle_id} "
            f"({result.entity_type}/{result.side}/{result.metric})"
        )
        return success_result(
            result,
            summary=summary,
            operation="get_battle_ranking",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["GET_BATTLE_RANKING_DESCRIPTION", "register"]
