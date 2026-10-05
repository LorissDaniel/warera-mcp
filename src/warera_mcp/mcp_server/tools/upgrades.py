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
    PlayerContextInput,
    RequiredIdentifier,
    player_context_credentials,
)

CompanyUpgrade = Literal["storage", "automatedEngine", "breakRoom"]
RegionUpgrade = Literal["bunker", "base", "pacificationCenter"]


def respond(ctx: ToolContext, result: ToolResult, operation: str, summary: str) -> CallToolResult:
    return success_result(
        result,
        operation=operation,
        summary=summary,
        max_bytes=runtime_of(ctx).settings.max_output_bytes,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_company_upgrades",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Inspect company storage, automatedEngine and breakRoom upgrades: reported "
            "levels/status,  "
            "money/concrete/steel investments, dependent-user counts and change/activation dates. "
            "Omit upgrade_types for all three. Disabled/pending upgrades remain distinct from "
            "active  "
            "levels. absent_upgrade_types means no record was returned; unavailable_upgrade_types "
            "means a failed/invalid read and partial=true. Missing levels/costs are unknown, "
            "not zero.  "
            "Use get_company_overview/get_item_details for production and configured costs."
        ),
    )
    @tool_errors("get_company_upgrades")
    async def get_company_upgrades(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        company_id: RequiredIdentifier,
        upgrade_types: Annotated[
            list[CompanyUpgrade] | None,
            Field(
                min_length=1,
                max_length=3,
                description="Optional subset of the three company upgrade types.",
            ),
        ] = None,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.upgrades.get_upgrades(
            entity_type="company",
            entity_id=company_id,
            upgrade_types=upgrade_types,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_company_upgrades",
            f"{len(result.upgrades)} company upgrades; partial={result.partial}",
        )

    @mcp.tool(
        name="get_region_upgrades",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Inspect regional bunker, base and pacificationCenter upgrades with reported "
            "status/level,  "
            "investments and activation/upgrade/downgrade dates. Omit upgrade_types for all three. "
            "Disabled/pending records are preserved. absent_upgrade_types means no record "
            "returned;  "
            "unavailable_upgrade_types means a failed/invalid read and partial=true. Missing costs "
            "or levels are unknown. Use get_region for territory/deposit context and "
            "get_game_rules  "
            "for configured effects; reported investments are not current treasury."
        ),
    )
    @tool_errors("get_region_upgrades")
    async def get_region_upgrades(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        region_id: RequiredIdentifier,
        upgrade_types: Annotated[
            list[RegionUpgrade] | None,
            Field(
                min_length=1,
                max_length=3,
                description="Optional subset of the three regional upgrade types.",
            ),
        ] = None,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.upgrades.get_upgrades(
            entity_type="region",
            entity_id=region_id,
            upgrade_types=upgrade_types,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_region_upgrades",
            f"{len(result.upgrades)} region upgrades; partial={result.partial}",
        )
