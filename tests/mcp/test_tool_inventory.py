"""Tool discovery: the advertised MCP surface is exactly the approved inventory."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from tests.conftest import UpstreamStub, run_mcp
from warera_mcp.config import Settings
from warera_mcp.mcp_server.tools.registry import (
    APPROVED_TOOL_NAMES,
    ToolInventoryError,
    register_tools,
    registered_tool_names,
    verify_inventory,
)

#: Words that would reveal a raw passthrough or generic query tool.
FORBIDDEN_TOOL_NAME_FRAGMENTS = ("raw", "trpc", "endpoint", "procedure", "request", "generic")

#: Argument names that would let a caller choose an upstream operation.
FORBIDDEN_ARGUMENT_NAMES = ("procedure", "endpoint", "path", "url", "query", "body", "payload")


def list_tools(settings: Settings, stub: UpstreamStub) -> list[Any]:
    captured: list[Any] = []

    async def scenario(session: Any) -> None:
        response = await session.list_tools()
        captured.extend(response.tools)

    run_mcp(settings, stub, scenario)
    return captured


def test_approved_inventory_is_fully_registered(settings: Settings, stub: UpstreamStub) -> None:
    tools = list_tools(settings, stub)
    assert {tool.name for tool in tools} == APPROVED_TOOL_NAMES


def test_every_tool_is_advertised_as_read_only(settings: Settings, stub: UpstreamStub) -> None:
    for tool in list_tools(settings, stub):
        assert tool.annotations is not None, tool.name
        assert tool.annotations.readOnlyHint is True, tool.name
        assert tool.annotations.destructiveHint is False, tool.name
        assert tool.annotations.openWorldHint is True, tool.name


def test_no_generic_or_raw_passthrough_tool_exists(settings: Settings, stub: UpstreamStub) -> None:
    for tool in list_tools(settings, stub):
        lowered = tool.name.lower()
        assert not any(fragment in lowered for fragment in FORBIDDEN_TOOL_NAME_FRAGMENTS), tool.name


def test_no_tool_accepts_an_upstream_operation_selector(
    settings: Settings, stub: UpstreamStub
) -> None:
    for tool in list_tools(settings, stub):
        properties = set(tool.inputSchema.get("properties", {}))
        assert properties.isdisjoint(FORBIDDEN_ARGUMENT_NAMES), (tool.name, properties)


def test_tool_descriptions_are_concise_and_explain_scope(
    settings: Settings, stub: UpstreamStub
) -> None:
    for tool in list_tools(settings, stub):
        description = tool.description or ""
        assert 60 <= len(description) <= 600, (tool.name, len(description))
        assert description.endswith("."), tool.name
        lowered = description.lower()
        assert "not a" in lowered or "not " in lowered or "does not" in lowered, tool.name


def test_tool_names_are_verb_noun_and_unique(settings: Settings, stub: UpstreamStub) -> None:
    names = [tool.name for tool in list_tools(settings, stub)]
    assert len(names) == len(set(names))
    for name in names:
        assert name.split("_")[0] in {"get", "search"}, name


def test_list_schemas_declare_object_arguments(settings: Settings, stub: UpstreamStub) -> None:
    for tool in list_tools(settings, stub):
        assert tool.inputSchema["type"] == "object", tool.name
        assert tool.outputSchema is None, tool.name  # structured content is unconstrained JSON


def test_server_instructions_carry_the_credential_warning() -> None:
    from warera_mcp.auth.warnings import CLEARTEXT_CREDENTIAL_WARNING
    from warera_mcp.mcp_server.instructions import SERVER_INSTRUCTIONS

    assert CLEARTEXT_CREDENTIAL_WARNING in SERVER_INSTRUCTIONS
    assert "READ-ONLY" in SERVER_INSTRUCTIONS
    assert "untrusted" in SERVER_INSTRUCTIONS
    assert "combine" in SERVER_INSTRUCTIONS
    assert "companies" in SERVER_INSTRUCTIONS and "market books" in SERVER_INSTRUCTIONS
    assert "best ask" in SERVER_INSTRUCTIONS and "best bid" in SERVER_INSTRUCTIONS
    assert "opportunity cost" in SERVER_INSTRUCTIONS
    assert "available" in SERVER_INSTRUCTIONS and "stock" in SERVER_INSTRUCTIONS
    assert "call `get_item_details`" in SERVER_INSTRUCTIONS
    assert "do not infer ingredients" in SERVER_INSTRUCTIONS


def test_descriptions_explain_recipe_fallback_and_available_player_skills(
    settings: Settings, stub: UpstreamStub
) -> None:
    tools = {tool.name: tool for tool in list_tools(settings, stub)}
    company_description = tools["get_company_overview"].description or ""
    player_description = tools["get_player"].description or ""
    assert "official game configuration" in company_description
    assert "current skill summary" in player_description


def test_registry_refuses_unexpected_tool_registrations() -> None:
    mcp = FastMCP("inventory-guard")

    @mcp.tool(name="raw_query", description="not allowed")
    async def raw_query() -> str:  # pragma: no cover - registration must fail first
        return "nope"

    with pytest.raises(ToolInventoryError, match="unexpected"):
        register_tools(mcp)


def test_registry_refuses_missing_tools() -> None:
    mcp = FastMCP("inventory-guard-empty")
    with pytest.raises(ToolInventoryError, match="missing"):
        verify_inventory(mcp)


def test_register_tools_is_idempotent_for_the_approved_set() -> None:
    mcp = FastMCP("inventory-idempotent")
    assert register_tools(mcp) == APPROVED_TOOL_NAMES
    assert registered_tool_names(mcp) == set(APPROVED_TOOL_NAMES)
