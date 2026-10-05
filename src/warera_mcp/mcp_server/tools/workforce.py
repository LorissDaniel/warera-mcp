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
    OptionalIdentifier,
    PlayerContextInput,
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
        name="get_work_offer",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get one public work offer by work_offer_id OR company_id (exactly one). Returns "
            "offer,  "
            "employer/company/region links, gross/net wages, remaining/initial quantity, explicit "
            "minimum energy/production/level and citizenship requirements when reported. Use "
            "get_work_market for filtered discovery and wage benchmarks. Requirements do not prove "
            "that a player is eligible or a position is still available; snapshots may change. "
            "Wages retain API units, not inferred hourly pay. Missing offers return NOT_FOUND."
        ),
    )
    @tool_errors("get_work_offer")
    async def get_work_offer(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        work_offer_id: OptionalIdentifier = None,
        company_id: OptionalIdentifier = None,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.workforce.get_work_offer(
            work_offer_id=work_offer_id,
            company_id=company_id,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(ctx, result, "get_work_offer", f"Work offer {result.offer.id}")

    @mcp.tool(
        name="get_workers",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Requires request-scoped API_KEY, not JWT. Inspect company workers OR workers grouped "
            "under a user's owned companies (exactly one company_id/user_id). user_id identifies "
            "the employer's company portfolio, not that player's personal employment. Returns "
            "worker  "
            "IDs/user/employer/company links, wages, fidelity and timestamps. offset/limit page "
            "the  "
            "flattened snapshot locally; company summaries cap at 20. User scope also reads the "
            "API's reported total count. Missing reads stay partial; authenticated data is "
            "never cached."
        ),
    )
    @tool_errors("get_workers")
    async def get_workers(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        user_id: OptionalIdentifier = None,
        company_id: OptionalIdentifier = None,
        offset: Offset = 0,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.workforce.get_workers(
            user_id=user_id,
            company_id=company_id,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_workers",
            f"{len(result.workers)} workers from snapshot {result.snapshot_count}",
        )
