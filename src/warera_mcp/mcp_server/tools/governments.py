"""Additional semantic MCP reads, with explicit visibility, units and pagination."""

from __future__ import annotations

from typing import Annotated

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
        name="get_country_government",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get reported government office holders for a country: president, vice-president, "
            "ministers and a local offset/limit page of congress member IDs, plus announcement "
            "dates.  "
            "Follow IDs with get_player; use get_country_overview for country facts. Missing roles "
            "or member lists are unknown, not vacant offices or zero congress members. Counts "
            "cover  "
            "the returned roster, which can change between calls. Announcement dates are capped "
            "at 20 (dates_truncated); no announcement text or effective permissions are inferred."
        ),
    )
    @tool_errors("get_country_government")
    async def get_country_government(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        country_id: RequiredIdentifier,
        offset: Offset = 0,
        limit: MediumLimit = 20,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.governments.get_government(
            country_id=country_id,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx, result, "get_country_government", f"Reported government of {country_id}"
        )
