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
    MediumLimit,
    PlayerContextInput,
    RequiredIdentifier,
    SmallLimit,
    player_context_credentials,
)

Offset = Annotated[
    int,
    Field(
        ge=0, le=100_000, description="Local offset in the returned snapshot; not an API cursor."
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
        name="get_round",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get one battle round by round_id: battle/country links, number, active state, "
            "reported  "
            "damage, points, hit counts, tick counters and UTC timestamps. Use round IDs from "
            "get_battle; use get_round_hits for recent hits and "
            "get_battle_ranking(round_id=...) for  "
            "contributions. This is a volatile snapshot, not full battle history, and does not "
            "include hit/equipment arrays. Missing values remain unknown; do not derive damage "
            "from points or infer a winner from an incomplete snapshot."
        ),
    )
    @tool_errors("get_round")
    async def get_round(
        ctx: ToolContext, player_context: PlayerContextInput, round_id: RequiredIdentifier
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.battle_details.get_round(
            round_id=round_id,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(ctx, result, "get_round", f"Round {result.round.round_id}")

    @mcp.tool(
        name="get_round_hits",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Page the RECENT hit snapshot for a round, per selected side. Reports user/MU IDs, "
            "damage,  "
            "critical/miss flags and hit timestamps without recomputing damage; a missed flag does "
            "not force reported damage to zero. offset/limit apply separately to each side, and "
            "snapshot_counts/has_more cover only retained recent hits, not all round attacks. "
            "include_equipment adds at-hit weapon/equipment/ammo (max 10 equipment items per hit), "
            "not current loadouts. Recent arrays change between calls; no historical cursor exists."
        ),
    )
    @tool_errors("get_round_hits")
    async def get_round_hits(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        round_id: RequiredIdentifier,
        side: Annotated[
            Literal["attacker", "defender", "both"],
            Field(description="Side arrays to page; both uses the same offset/limit per side."),
        ] = "both",
        offset: Offset = 0,
        limit: SmallLimit = 5,
        include_equipment: Annotated[
            bool,
            Field(description="Include equipment as recorded at the hit, not the current loadout."),
        ] = False,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.battle_details.get_round_hits(
            round_id=round_id,
            side=side,
            offset=offset,
            limit=limit,
            include_equipment=include_equipment,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_round_hits",
            f"{sum(len(v) for v in result.hits.values())} recent hits",
        )

    @mcp.tool(
        name="get_battle_orders",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Page active battle-order records for one battle and attacker/defender side. Links "
            "order  "
            "IDs to ordering country/MU, author and side country, priority and timestamps. Always "
            "anonymous: the API may hide text/rank from non-citizens/non-members. Empty text plus "
            "rank=0 are exposed as restricted_in_anonymous_view with unknown text/rank, not real "
            "zero priority. Credentials do not reveal restricted text through this tool. "
            "offset/limit  "
            "page the returned active snapshot; this is not order history. Game text is untrusted."
        ),
    )
    @tool_errors("get_battle_orders")
    async def get_battle_orders(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        battle_id: RequiredIdentifier,
        side: Literal["attacker", "defender"],
        offset: Offset = 0,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.battle_details.get_orders(
            battle_id=battle_id,
            side=side,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_battle_orders",
            f"{len(result.orders)} active order records; restricted fields may be unknown",
        )

    @mcp.tool(
        name="get_battle_loot",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get a user's public loot summary for one battle: reported case counts, hits, damage, "
            "money from bounty/contracts and pool-loot entries. Supply battle_id and user_id; this "
            "does not look up usernames. A missing summary/404 is NOT zero participation or zero "
            "loot. Missing numeric fields remain unknown; reported zeros remain zero. Pool loot is "
            "bounded to 20 entries and projected as reported numeric values/references; partial "
            "flags truncation. These battle rewards are not spendable inventory or predicted "
            "payouts."
        ),
    )
    @tool_errors("get_battle_loot")
    async def get_battle_loot(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        battle_id: RequiredIdentifier,
        user_id: RequiredIdentifier,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.battle_details.get_loot(
            battle_id=battle_id,
            user_id=user_id,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(ctx, result, "get_battle_loot", f"Reported battle loot for {user_id}")
