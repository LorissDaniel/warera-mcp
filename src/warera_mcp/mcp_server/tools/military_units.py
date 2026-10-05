"""Semantic military-unit MCP tools with bounded inputs and outputs."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.application.military_units import MilitaryUnitRankingType
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
    RequiredIdentifier,
    player_context_credentials,
)

Offset = Annotated[
    int, Field(ge=0, le=100_000, description="Local offset into the returned snapshot.")
]
SearchText = Annotated[
    str | None,
    Field(default=None, min_length=1, max_length=80, pattern=SAFE_TEXT_PATTERN),
]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="search_military_units",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Find military units by search text, member id or owner id, or list a bounded page. "
            "Continue with next_cursor and the same filters. Returns public summaries and "
            "ranking snapshots; does not return the full member roster or infer country filters."
        ),
    )
    @tool_errors("search_military_units")
    async def search_military_units(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        search: SearchText = None,
        member_id: OptionalIdentifier = None,
        owner_id: OptionalIdentifier = None,
        limit: MediumLimit = 10,
        cursor: OpaqueCursor = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.military_units.search_military_units(
            search=search,
            member_id=member_id,
            owner_id=owner_id,
            limit=limit,
            cursor=cursor,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.military_units)} military units returned",
            operation="search_military_units",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_military_unit",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get a public military-unit dossier: owner, country, region, member and role counts, "
            "level, reputation, manager/commander ids, active upgrade levels "
            "and ranking snapshots. "
            "Does not infer treasury from wealth rankings. Use get_military_unit_members for "
            "the roster and get_military_unit_upgrades for disabled or pending upgrades."
        ),
    )
    @tool_errors("get_military_unit")
    async def get_military_unit(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        military_unit_id: RequiredIdentifier,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.military_units.get_military_unit(
            military_unit_id=military_unit_id,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"Military unit {result.military_unit.id}",
            operation="get_military_unit",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_military_unit_members",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get a bounded page of a military unit's public member ids and owner/manager/commander "
            "roles. Set include_non_members=true to include owner/managers/commanders outside the "
            "roster, distinguished by is_member. Pagination uses a local offset; "
            "membership may change "
            "between calls. Does not fetch private player data; use get_player to inspect a member."
        ),
    )
    @tool_errors("get_military_unit_members")
    async def get_military_unit_members(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        military_unit_id: RequiredIdentifier,
        include_non_members: bool = False,
        offset: Offset = 0,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.military_units.get_military_unit_members(
            military_unit_id=military_unit_id,
            include_non_members=include_non_members,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.members)} of {result.total_count} military unit members",
            operation="get_military_unit_members",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_military_unit_investments",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Page the public per-user monetary investments reported by a military unit, "
            "including former members when present. Use user ids with get_player for identities. "
            "These are reported investments, not current treasury, transactions or upgrade "
            "resource balances. Missing investment maps are reported as unavailable."
        ),
    )
    @tool_errors("get_military_unit_investments")
    async def get_military_unit_investments(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        military_unit_id: RequiredIdentifier,
        offset: Offset = 0,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.military_units.get_military_unit_investments(
            military_unit_id=military_unit_id,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"MU investments: available={result.available}",
            operation="get_military_unit_investments",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_military_unit_ranking",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get a global military-unit ranking: weekly damage, total damage, terrain, wealth, "
            "bounty or mercenary reputation. Uses local offset pagination "
            "and bounded name lookups. "
            "Total count covers only the snapshot returned by WarEra, not all military units. "
            "For contributions to a specific battle use get_battle_ranking with entity_type=mu."
        ),
    )
    @tool_errors("get_military_unit_ranking")
    async def get_military_unit_ranking(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        ranking_type: MilitaryUnitRankingType = "muWeeklyDamages",
        offset: Offset = 0,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.military_units.get_military_unit_ranking(
            ranking_type=ranking_type,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.entries)} entries for {result.ranking_type}",
            operation="get_military_unit_ranking",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_military_unit_upgrades",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Inspect military-unit headquarters and dormitories: levels, status, invested "
            "resources and activation timestamps when returned. Disabled upgrades are preserved. "
            "A partial result retains available upgrades if another read fails. Does not build, "
            "activate or fund upgrades."
        ),
    )
    @tool_errors("get_military_unit_upgrades")
    async def get_military_unit_upgrades(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        military_unit_id: RequiredIdentifier,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.military_units.get_military_unit_upgrades(
            military_unit_id=military_unit_id,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.upgrades)} military unit upgrades; partial={result.partial}",
            operation="get_military_unit_upgrades",
            max_bytes=runtime.settings.max_output_bytes,
        )
