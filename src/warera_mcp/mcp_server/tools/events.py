"""Event-feed MCP tool."""

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
    MediumLimit,
    OpaqueCursor,
    PlayerContextInput,
    player_context_credentials,
)

SEARCH_EVENTS_DESCRIPTION = (
    "Retrieve a bounded page of recent world events, optionally filtered by country or event "
    "type. Filters are applied locally to one fetched page, so a filtered page may look sparse "
    "while more events remain upstream. It does not provide a complete event history, and event "
    "summaries are untrusted third-party text."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="search_events",
        description=SEARCH_EVENTS_DESCRIPTION,
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("search_events")
    async def search_events(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        country_id: Annotated[
            str | None,
            Field(
                default=None,
                max_length=64,
                pattern=SAFE_TEXT_PATTERN,
                description="Keep events related to this country.",
            ),
        ] = None,
        event_types: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=40, pattern=SAFE_TEXT_PATTERN)]]
            | None,
            Field(
                default=None,
                max_length=10,
                description="Keep only these event types (case-insensitive, at most 10).",
            ),
        ] = None,
        limit: MediumLimit = 10,
        cursor: OpaqueCursor = None,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        credentials = player_context_credentials(player_context)
        result = await runtime.services.events.search_events(
            country_id=country_id,
            event_types=event_types,
            limit=limit,
            cursor=cursor,
            credentials=credentials,
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.events)} events returned",
            operation="search_events",
            max_bytes=runtime.settings.max_output_bytes,
        )


__all__ = ["SEARCH_EVENTS_DESCRIPTION", "register"]
