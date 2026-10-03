"""Battle-facing MCP tools."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.domain.models import SearchBattlesResult
from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    SAFE_TEXT_PATTERN,
    OpaqueCursor,
    RequiredIdentifier,
    SmallLimit,
)


def _battle_list_summary(result: SearchBattlesResult) -> str:
    """Render one battle page for clients that only display MCP text content."""
    battles = getattr(result, "battles", [])
    lines: list[str] = []
    for index, battle in enumerate(battles, start=1):
        kind = "Resistenza" if battle.type == "resistance" else "Guerra"
        attacker = battle.attacker
        defender = battle.defender
        attacker_name = (attacker.country_name if attacker else None) or (
            attacker.country_id if attacker else "?"
        )
        defender_name = (defender.country_name if defender else None) or (
            defender.country_id if defender else "?"
        )
        attacker_damage = attacker.damages if attacker and attacker.damages is not None else 0
        defender_damage = defender.damages if defender and defender.damages is not None else 0
        lines.append(
            f"{index}. {kind}: {attacker_name} vs {defender_name} "
            f"(danni {attacker_damage:g}-{defender_damage:g})"
        )

    page = getattr(result, "page", None)
    continuation = ""
    if page is not None and page.has_more:
        continuation = f"; altre battaglie disponibili, next_cursor={page.next_cursor}"
    return f"{len(battles)} battaglie restituite: " + "; ".join(lines) + continuation
SEARCH_BATTLES_DESCRIPTION = (
    "List battles, optionally filtered by country and active state. Each side includes the "
    "human-readable country name and id. Without a filter this is the first upstream page, and "
    "its ordering is not guaranteed, so do not describe it as 'latest'. For a battle leaderboard "
    "use get_battle_ranking."
)

GET_BATTLE_DESCRIPTION = (
    "Get a battle's status: sides, current round and optionally a live snapshot. Live values "
    "are volatile snapshots with an explicit tick timestamp. It does not return last-hits "
    "lists or equipment payloads."
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
            str | None,
            Field(
                default=None,
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="Filter by country id.",
            ),
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
            summary=_battle_list_summary(result),
            operation="search_battles",
            max_bytes=runtime.settings.max_output_bytes,
            summary_limit=4_000,
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
        attacker = result.battle.attacker
        defender = result.battle.defender
        attacker_name = (attacker.country_name if attacker else None) or (
            attacker.country_id if attacker else "?"
        )
        defender_name = (defender.country_name if defender else None) or (
            defender.country_id if defender else "?"
        )
        return success_result(
            result,
            summary=(
                f"Battle {result.battle.id}: {attacker_name} vs {defender_name} is {state}"
            ),
            operation="get_battle",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["GET_BATTLE_DESCRIPTION", "SEARCH_BATTLES_DESCRIPTION", "register"]
