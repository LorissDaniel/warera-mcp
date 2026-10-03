"""Approved read-only MCP tool registry.

The registry is the single source of truth for what the server exposes. It
registers exactly the approved tool modules and then verifies the registered
inventory against :data:`APPROVED_TOOL_NAMES`, so a new tool cannot ship without
an explicit, reviewable change to this list.
"""

from __future__ import annotations

from types import ModuleType

from mcp.server.fastmcp import FastMCP

from warera_mcp.mcp_server.tools import (
    battles,
    companies,
    events,
    market,
    players,
    rankings,
    world,
)

#: The complete, reviewed tool inventory for this release.
APPROVED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_player",
        "get_player_companies",
        "get_company_overview",
        "get_country_overview",
        "get_country_wars",
        "get_region",
        "get_market_price",
        "search_market",
        "get_work_market",
        "search_battles",
        "get_battle",
        "get_battle_ranking",
        "search_events",
    }
)

_TOOL_MODULES: tuple[ModuleType, ...] = (
    players,
    companies,
    world,
    market,
    battles,
    rankings,
    events,
)


class ToolInventoryError(RuntimeError):
    """Raised when the registered tools do not match the approved inventory."""


def registered_tool_names(mcp: FastMCP) -> set[str]:
    """Return the names of every tool currently registered on *mcp*."""
    return {tool.name for tool in mcp._tool_manager.list_tools()}


def verify_inventory(mcp: FastMCP) -> frozenset[str]:
    """Assert that the registered tools match the approved read-only inventory."""
    names = registered_tool_names(mcp)
    missing = APPROVED_TOOL_NAMES - names
    unexpected = names - APPROVED_TOOL_NAMES
    if missing or unexpected:
        raise ToolInventoryError(
            "registered tool inventory does not match the approved read-only list "
            f"(missing={sorted(missing)}, unexpected={sorted(unexpected)})"
        )
    return APPROVED_TOOL_NAMES


def register_tools(mcp: FastMCP) -> frozenset[str]:
    """Register every approved tool module and verify the resulting inventory."""
    for module in _TOOL_MODULES:
        module.register(mcp)
    return verify_inventory(mcp)


__all__ = [
    "APPROVED_TOOL_NAMES",
    "ToolInventoryError",
    "register_tools",
    "registered_tool_names",
    "verify_inventory",
]
