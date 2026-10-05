"""Company-facing MCP tools."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import quoted, success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    SAFE_TEXT_PATTERN,
    PlayerContextInput,
    RequiredIdentifier,
    player_context_credentials,
)

GET_PLAYER_COMPANIES_DESCRIPTION = (
    "List the companies a player owns, with product, location, workforce and optional production "
    "bonus. This answers ownership questions in one call instead of many company lookups. It is "
    "not a country-wide company directory."
)

#: Matches the default ``Settings.max_fanout``; a lower configured cap still clamps
#: (with a warning), but the advertised range is reachable out of the box.
MAX_COMPANIES_PER_CALL = 10

GET_COMPANY_OVERVIEW_DESCRIPTION = (
    "Get one company's details: what it produces, where it sits, its workforce and its production "
    "bonus (normalized to a fraction). Includes a validated recipe from an explicit catalog or "
    "official game configuration when available; recipe inputs describe configured requirements, "
    "not current stock."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_recommended_regions",
        description=(
            "Get the game's ranked regions and production bonus components for an item. "
            "Set include_deposit=false to request bonuses without deposits. Requires API key "
            "or JWT; API key is preferred. Coverage is the game's recommendations, not all regions."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_recommended_regions")
    async def get_recommended_regions(
        ctx: ToolContext,
        item_code: Annotated[str, Field(min_length=1, max_length=64, pattern=SAFE_TEXT_PATTERN)],
        player_context: PlayerContextInput,
        include_deposit: bool = True,
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.locations.get_recommended_regions(
            item_code=item_code,
            include_deposit=include_deposit,
            limit=limit,
            offset=offset,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.regions)} recommended regions for {item_code}",
            operation="get_recommended_regions",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_player_companies",
        description=GET_PLAYER_COMPANIES_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_player_companies")
    async def get_player_companies(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        user_id: Annotated[
            str | None,
            Field(
                default=None,
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="WarEra user id (preferred).",
            ),
        ] = None,
        username: Annotated[
            str | None,
            Field(
                default=None,
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="Exact in-game username.",
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_COMPANIES_PER_CALL,
                default=MAX_COMPANIES_PER_CALL,
                description=f"Maximum companies to return (1-{MAX_COMPANIES_PER_CALL}).",
            ),
        ] = MAX_COMPANIES_PER_CALL,
        offset: Annotated[
            int, Field(ge=0, le=10_000, default=0, description="Local offset into the owned list.")
        ] = 0,
        include_bonus: Annotated[
            bool, Field(default=True, description="Fetch each company's production bonus.")
        ] = True,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.companies.get_player_companies(
            user_id=user_id,
            username=username,
            limit=limit,
            offset=offset,
            include_bonus=include_bonus,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        returned = len(result.companies)
        summary = (
            f"{returned} of {result.total_count} companies for "
            f"{quoted(result.player.username or result.player.id)}"
        )
        return success_result(
            result,
            summary=summary,
            operation="get_player_companies",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_company_overview",
        description=GET_COMPANY_OVERVIEW_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_company_overview")
    async def get_company_overview(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        company_id: RequiredIdentifier,
        include_region_context: Annotated[
            bool, Field(default=False, description="Also resolve the company's region.")
        ] = False,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.companies.get_company_overview(
            company_id=company_id,
            include_region_context=include_region_context,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        name = result.company.name or result.company.id
        summary = f"Company {quoted(name)}"
        if result.company.item_code:
            summary += f" produces {result.company.item_code}"
        return success_result(
            result,
            summary=summary,
            operation="get_company_overview",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = [
    "GET_COMPANY_OVERVIEW_DESCRIPTION",
    "GET_PLAYER_COMPANIES_DESCRIPTION",
    "register",
]
