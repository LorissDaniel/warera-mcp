"""Anonymous tools for official game rules, item configuration and schedules."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import READ_ONLY_ANNOTATIONS, SAFE_TEXT_PATTERN

TOPIC = Literal[
    "player",
    "combat",
    "companies",
    "workers",
    "military_units",
    "upgrades",
    "politics",
    "world",
    "missions",
    "items",
    "skills",
]
Code = Annotated[str, Field(min_length=1, max_length=64, pattern=SAFE_TEXT_PATTERN)]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_game_rules",
        description=(
            "Get bounded official configuration facts for one supported game topic, including "
            "item and skill discovery. Results are timestamped snapshots; this does not provide "
            "every game mechanic."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_game_rules")
    async def get_game_rules(
        ctx: ToolContext,
        topic: TOPIC,
        offset: Annotated[int, Field(ge=0, default=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=50, default=20)] = 20,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.game_configuration.get_rules(
            topic, offset, limit, call_id(ctx)
        )
        return success_result(
            result,
            summary=f"Official {topic} configuration ({result.total_count} records)",
            operation="get_game_rules",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_skill_progression",
        description=(
            "Get the official levels, values, costs and unlock requirements for one skill. "
            "Level values retain their configured scale; this does not infer missing "
            "progression rules."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_skill_progression")
    async def get_skill_progression(
        ctx: ToolContext,
        skill_code: Code,
        min_level: Annotated[int, Field(ge=0, default=0)] = 0,
        max_level: Annotated[int | None, Field(ge=0, default=None)] = None,
        offset: Annotated[int, Field(ge=0, default=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=50, default=20)] = 20,
    ) -> CallToolResult:
        if max_level is not None and max_level < min_level:
            from warera_mcp import errors as app_errors

            raise app_errors.invalid_input(
                "max_level must be greater than or equal to min_level", "get_skill_progression"
            )
        runtime = runtime_of(ctx)
        result = await runtime.services.game_configuration.get_skill(
            skill_code, min_level, max_level, offset, limit, call_id(ctx)
        )
        return success_result(
            result,
            summary=f"Skill {result.skill_code}: {result.total_count} configured levels",
            operation="get_skill_progression",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_item_details",
        description=(
            "Get official configuration and a validated production recipe for one item code. "
            "Configuration items can differ from market-eligible items; this does not "
            "guarantee market availability."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_item_details")
    async def get_item_details(ctx: ToolContext, item_code: Code) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.game_configuration.get_item(item_code, call_id(ctx))
        return success_result(
            result,
            summary=f"Official configuration for {result.item['item_code']}",
            operation="get_item_details",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_game_schedule",
        description=(
            "Get official upcoming reset, regeneration and election timestamps from the game "
            "schedule. Results are UTC snapshots and may include missing or already-passed "
            "fields; it does not calculate replacement dates."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_game_schedule")
    async def get_game_schedule(ctx: ToolContext) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.game_configuration.get_schedule(call_id(ctx))
        return success_result(
            result,
            summary=f"Official game schedule: {len(result.schedule)} timestamps",
            operation="get_game_schedule",
            max_bytes=runtime.settings.max_output_bytes,
        )
