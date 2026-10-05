"""One shared price snapshot for multi-material MCP reads."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import UpstreamStub, run_mcp
from warera_mcp.config import Settings

PRICES = {"iron": 0.1, "steel": 1.8, "fish": 3.7, "wood": 0.2}


def test_subset_deduplication_and_shared_snapshot(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("itemTrading.getPrices", PRICES)

    async def scenario(session: Any) -> None:
        subset = await session.call_tool(
            "get_market_prices",
            {
                "item_codes": ["iron", "steel", "iron"],
                "player_context": {"api_key": "test-key"},
            },
        )
        assert subset.isError is False
        data = subset.structuredContent
        assert data["prices"] == {"steel": 1.8, "iron": 0.1}
        assert list(data["prices"]) == ["steel", "iron"]
        assert data["requested_item_codes"] == ["iron", "steel"]
        assert data["missing_item_codes"] == []
        assert data["partial"] is False and data["truncated"] is False
        full = await session.call_tool("get_market_prices", {})
        assert full.structuredContent["prices"] == PRICES
        assert "requested_item_codes" not in full.structuredContent
        single = await session.call_tool("get_market_price", {"item_code": "steel"})
        assert single.structuredContent["price"] == data["prices"]["steel"]
        assert single.structuredContent["observed_at"] == data["observed_at"]

    run_mcp(settings, stub, scenario)
    assert stub.inputs("itemTrading.getPrices") == [{}]  # one read, filtering stays local
    assert stub.methods == {"GET"}
    assert all("x-api-key" not in req.headers for req in stub.requests)


def test_missing_quotes_preserve_available_prices(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("itemTrading.getPrices", PRICES)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_market_prices", {"item_codes": ["iron", "unknown"]})
        assert result.isError is False
        data = result.structuredContent
        assert data["prices"] == {"iron": 0.1}
        assert data["missing_item_codes"] == ["unknown"]
        assert data["partial"] is True and data["truncated"] is False
        absent = await session.call_tool("get_market_prices", {"item_codes": ["unknown"]})
        assert absent.structuredContent["prices"] == {}
        assert absent.structuredContent["partial"] is True
        assert any("do not assume zero" in warning for warning in data["warnings"])

    run_mcp(settings, stub, scenario)


def test_limit_applies_after_filtering(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("itemTrading.getPrices", PRICES)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_market_prices",
            {
                "item_codes": ["iron", "wood"],
                "limit": 1,
            },
        )
        data = result.structuredContent
        assert data["prices"] == {"wood": 0.2}
        assert data["truncated"] is True and data["partial"] is True
        assert data["missing_item_codes"] == []

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    "arguments",
    [
        {"item_codes": []},
        {"item_codes": ["iron"] * 65},
        {"item_codes": [""]},
        {"item_codes": ["iron\n"]},
        {"item_codes": ["x" * 65]},
        {"limit": 0},
    ],
)
def test_invalid_multi_price_inputs_never_reach_upstream(
    settings: Settings,
    stub: UpstreamStub,
    arguments: dict[str, Any],
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_market_prices", arguments)
        assert result.isError is True

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


def test_price_tool_guidance_prefers_one_call(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(session: Any) -> None:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        bulk = tools["get_market_prices"]
        assert "two or more" in bulk.description
        assert "ONE call" in bulk.description
        assert "get_market_prices" in tools["get_market_price"].description
        assert "item_codes" in bulk.inputSchema["properties"]
        from warera_mcp.mcp_server.instructions import SERVER_INSTRUCTIONS

        assert "get_market_prices` ONCE with item_codes" in SERVER_INSTRUCTIONS

    run_mcp(settings, stub, scenario)
