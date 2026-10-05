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
    player_context_credentials,
)

Offset = Annotated[
    int,
    Field(
        ge=0, le=100_000, description="Local offset in the returned snapshot; not an API cursor."
    ),
]
GlobalRankingType = Literal[
    "weeklyCountryDamages",
    "weeklyCountryDamagesPerCitizen",
    "countryRegionDiff",
    "countryDevelopment",
    "countryActivePopulation",
    "countryDamages",
    "countryWealth",
    "countryProductionBonus",
    "countryBounty",
    "weeklyUserDamages",
    "userDamages",
    "userWealth",
    "userLevel",
    "userReferrals",
    "userSubscribers",
    "userTerrain",
    "userPremiumMonths",
    "userPremiumGifts",
    "userCasesOpened",
    "userGemsPurchased",
    "userBounty",
    "userSkinsOwned",
    "userMilestoneTiers",
    "userMissionsClaimed",
    "muWeeklyDamages",
    "muDamages",
    "muTerrain",
    "muWealth",
    "muBounty",
    "muReputation",
    "allianceInitialDevelopment",
    "allianceDevelopment",
    "allianceWeeklyDamages",
    "allianceDamages",
    "alliancePopulation",
    "allianceWeeklyDamagesPerCitizen",
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
        name="get_global_ranking",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Page a documented global country/user/alliance/MU ranking snapshot by ranking_type. "
            "Returns entity IDs, reported value/rank/tier and tier thresholds; country/MU links "
            "are retained where present. offset/limit are LOCAL: upstream returns the full "
            "snapshot.  "
            "snapshot_count is its size, not world population; pages can change. No historical "
            "week  "
            "selector is documented. Wealth is not inventory, and per-citizen metrics keep "
            "their own  "
            "units. Follow IDs with the corresponding tools; names are not expanded automatically."
        ),
    )
    @tool_errors("get_global_ranking")
    async def get_global_ranking(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        ranking_type: GlobalRankingType,
        offset: Offset = 0,
        limit: MediumLimit = 10,
    ) -> CallToolResult:
        result = await runtime_of(ctx).services.global_rankings.get_global_ranking(
            ranking_type=ranking_type,
            offset=offset,
            limit=limit,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return respond(
            ctx,
            result,
            "get_global_ranking",
            f"{len(result.entries)} entries of snapshot {result.snapshot_count}",
        )
