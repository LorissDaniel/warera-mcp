"""Battle-facing MCP tools."""

from __future__ import annotations

from typing import Annotated, Literal

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
    OptionalIdentifier,
    PlayerContextInput,
    RequiredIdentifier,
    SmallLimit,
    player_context_credentials,
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
        attacker_damage = (
            f"{attacker.damages:g}" if attacker and attacker.damages is not None else "?"
        )
        defender_damage = (
            f"{defender.damages:g}" if defender and defender.damages is not None else "?"
        )
        lines.append(
            f"{index}. {kind}: {attacker_name} vs {defender_name} "
            f"(danni {attacker_damage}-{defender_damage})"
        )

    page = getattr(result, "page", None)
    continuation = ""
    if page is not None and page.has_more:
        continuation = f"; altre battaglie disponibili, next_cursor={page.next_cursor}"
    return f"{len(battles)} battaglie restituite: " + "; ".join(lines) + continuation


SEARCH_BATTLES_DESCRIPTION = (
    "Search one battle page with upstream country_id, war_id, defender_region_id and is_active "
    "filters. Filters combine; a defender region is the attacked region, not any region on either "
    "side. Follow page.next_cursor with identical filters and direction; forward/backward is API "
    "pagination direction, not a promise of chronological order. Sides include country names/IDs "
    "when available. An unfiltered page is not necessarily the latest battles. Use get_battle for "
    "status and get_battle_ranking for contributions."
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
        player_context: PlayerContextInput,
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
        war_id: OptionalIdentifier = None,
        defender_region_id: OptionalIdentifier = None,
        direction: Annotated[
            Literal["forward", "backward"] | None,
            Field(description="Upstream pagination direction; keep it unchanged when continuing."),
        ] = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.battles.search_battles(
            country_id=country_id,
            is_active=is_active,
            limit=limit,
            cursor=cursor,
            war_id=war_id,
            defender_region_id=defender_region_id,
            direction=direction,
            credentials=credentials,
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
        player_context: PlayerContextInput,
        battle_id: RequiredIdentifier,
        include_live_status: Annotated[
            bool, Field(default=True, description="Also fetch the live round snapshot.")
        ] = True,
        include_history: Annotated[
            bool, Field(default=False, description="Include previous rounds when available.")
        ] = False,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.battles.get_battle(
            battle_id=battle_id,
            include_live_status=include_live_status,
            include_history=include_history,
            credentials=credentials,
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
            summary=(f"Battle {result.battle.id}: {attacker_name} vs {defender_name} is {state}"),
            operation="get_battle",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["GET_BATTLE_DESCRIPTION", "SEARCH_BATTLES_DESCRIPTION", "register"]
