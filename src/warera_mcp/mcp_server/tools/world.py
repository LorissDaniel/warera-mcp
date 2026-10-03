"""World-facing MCP tools: country overview, region detail, current wars."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import READ_ONLY_ANNOTATIONS, OptionalIdentifier

GET_COUNTRY_OVERVIEW_DESCRIPTION = (
    "Get selected verified facts about a country plus, optionally, its regions. Names are matched "
    "exactly and case-insensitively, and this is a current snapshot only. It does not report war "
    "relationships — use get_country_wars for those."
)

GET_REGION_DESCRIPTION = (
    "Get a region's detail by id or exact name: population, development, climate, deposit and "
    "optionally its country. Region names repeat across countries, so ambiguous matches are "
    "rejected and reported instead of guessed."
)

GET_COUNTRY_WARS_DESCRIPTION = (
    "Show the countries this country is currently listed as at war with, plus its active battles. "
    "This is a current snapshot from the country's war relationship field, not a war-history or "
    "treaty report; an empty result is not proof that no battles ever occurred."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_country_overview",
        description=GET_COUNTRY_OVERVIEW_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_country_overview")
    async def get_country_overview(
        ctx: ToolContext,
        country_id: OptionalIdentifier = None,
        country_name: Annotated[
            str | None, Field(default=None, max_length=64, description="Exact country name.")
        ] = None,
        include_regions: Annotated[
            bool, Field(default=False, description="Also list the country's regions.")
        ] = False,
        limit_regions: Annotated[
            int, Field(ge=1, le=20, default=10, description="Maximum regions to list (1-20).")
        ] = 10,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.world.get_country_overview(
            country_id=country_id,
            country_name=country_name,
            include_regions=include_regions,
            limit_regions=limit_regions,
            correlation_id=call_id(ctx),
        )
        summary = f"Country {result.country.name or result.country.id}"
        if result.country.population is not None:
            summary += f" (population {result.country.population})"
        if result.regions is not None:
            summary += f"; {len(result.regions)} regions listed"
        return success_result(
            result,
            summary=summary,
            operation="get_country_overview",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_region", description=GET_REGION_DESCRIPTION, annotations=READ_ONLY_ANNOTATIONS
    )
    @tool_errors("get_region")
    async def get_region(
        ctx: ToolContext,
        region_id: OptionalIdentifier = None,
        region_name: Annotated[
            str | None, Field(default=None, max_length=64, description="Exact region name.")
        ] = None,
        include_country: Annotated[
            bool, Field(default=True, description="Also resolve the linked country.")
        ] = True,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.world.get_region(
            region_id=region_id,
            region_name=region_name,
            include_country=include_country,
            correlation_id=call_id(ctx),
        )
        summary = f"Region {result.region.name or result.region.id}"
        if result.country is not None and result.country.name:
            summary += f" in {result.country.name}"
        return success_result(
            result,
            summary=summary,
            operation="get_region",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_country_wars",
        description=GET_COUNTRY_WARS_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_country_wars")
    async def get_country_wars(
        ctx: ToolContext,
        country_id: OptionalIdentifier = None,
        country_name: Annotated[
            str | None, Field(default=None, max_length=64, description="Exact country name.")
        ] = None,
        include_active_battles: Annotated[
            bool,
            Field(default=True, description="Also fetch active battles involving the country."),
        ] = True,
        limit_battles: Annotated[
            int, Field(ge=1, le=10, default=5, description="Maximum active battles (1-10).")
        ] = 5,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.world.get_country_wars(
            country_id=country_id,
            country_name=country_name,
            include_active_battles=include_active_battles,
            limit_battles=limit_battles,
            correlation_id=call_id(ctx),
        )
        summary = (
            f"{result.country.name or result.country.id} is at war with "
            f"{len(result.opponents)} countr{'y' if len(result.opponents) == 1 else 'ies'}"
        )
        if result.active_battles is not None:
            summary += f"; {len(result.active_battles)} active battles in this page"
        return success_result(
            result,
            summary=summary,
            operation="get_country_wars",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = [
    "GET_COUNTRY_OVERVIEW_DESCRIPTION",
    "GET_COUNTRY_WARS_DESCRIPTION",
    "GET_REGION_DESCRIPTION",
    "register",
]
