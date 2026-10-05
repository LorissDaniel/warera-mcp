"""Public cross-domain discovery and paginated country-player references."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    SAFE_TEXT_PATTERN,
    OpaqueCursor,
    PlayerContextInput,
    RequiredIdentifier,
    player_context_credentials,
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="get_country_players",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Discover public player IDs in one country with upstream limit/cursor pagination. "
            "Returns IDs and account creation timestamps, without profile fan-out. Follow "
            "page.next_cursor with the same country_id. Use get_player for skills, location, MU "
            "and activity; creation time is not joining time or last activity. A page is not "
            "the country's total or active population, and no active-only filter is documented."
        ),
    )
    @tool_errors("get_country_players")
    async def get_country_players(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        country_id: RequiredIdentifier,
        cursor: OpaqueCursor = None,
        limit: Annotated[
            int, Field(ge=1, le=100, description="Upstream player-reference page size.")
        ] = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.discovery.get_country_players(
            country_id=country_id,
            cursor=cursor,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            operation="get_country_players",
            summary=f"{len(result.players)} player references",
            max_bytes=runtime_of(ctx).settings.max_output_bytes,
        )

    @mcp.tool(
        name="search_entities",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Search public user/MU/country/region/party/alliance ID groups in one request. "
            "Returns candidate IDs only, not names, profiles or exact identity resolution. "
            "Use get_player(username=...) for an exact user and linked domain tools for details. "
            "offset/limit are LOCAL per group over the API's retained matches; snapshot_counts "
            "and has_more do not describe every possible match. No historical/upstream cursor "
            "exists. Missing groups are unknown; an explicitly empty group is known empty."
        ),
    )
    @tool_errors("search_entities")
    async def search_entities(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        search: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                pattern=SAFE_TEXT_PATTERN,
                description="Game entity search text; results may be approximate.",
            ),
        ],
        offset: Annotated[
            int, Field(ge=0, le=100_000, description="Local offset per returned ID group.")
        ] = 0,
        limit: Annotated[int, Field(ge=1, le=100, description="Local maximum IDs per group.")] = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.discovery.search_entities(
            search=search,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            operation="search_entities",
            summary="Public entity ID candidates",
            max_bytes=runtime_of(ctx).settings.max_output_bytes,
        )
