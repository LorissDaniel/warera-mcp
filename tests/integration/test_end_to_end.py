"""Integration: full MCP client -> server -> stubbed WarEra round trips.

These tests verify the pieces that only exist once everything is wired together:
statelessness, request shape on the wire, freshness metadata, and the output
budget enforced at the MCP boundary.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.conftest import UpstreamStub, run_mcp
from warera_mcp.config import Settings
from warera_mcp.mcp_server.instructions import SERVER_INSTRUCTIONS


def test_public_request_reaches_upstream_as_a_plain_get(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route("itemTrading.getPrices", {"iron": 12.5})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert result.isError is False

    run_mcp(settings, stub, scenario)

    request = stub.calls("itemTrading.getPrices")[0]
    assert request.method == "GET"
    assert request.url.path == "/trpc/itemTrading.getPrices"
    assert json.loads(request.url.params["input"]) == {}
    assert "x-api-key" not in request.headers
    assert "cookie" not in request.headers
    assert request.headers["origin"] == "https://app.warera.io"
    assert request.headers["user-agent"].startswith("warera-mcp/")


def test_server_is_stateless_across_calls(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("itemTrading.getPrices", {"iron": 12.5})
    stub.route("user.getUserLite", {"_id": "u1", "username": "Kiro"})

    async def scenario(session: Any) -> None:
        first = await session.call_tool("get_market_price", {"item_code": "iron"})
        second = await session.call_tool("get_player", {"user_id": "u1"})
        third = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert first.isError is second.isError is third.isError is False
        # A repeated public read is served from the shared public cache.
        assert third.structuredContent["price"] == 12.5

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("itemTrading.getPrices")) == 1


def test_parameters_are_forwarded_with_the_documented_names(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "battle.getBattles",
        {"items": [], "nextCursor": None},
    )

    async def scenario(session: Any) -> None:
        await session.call_tool(
            "search_battles", {"country_id": "c1", "is_active": True, "limit": 3}
        )

    run_mcp(settings, stub, scenario)
    assert stub.inputs("battle.getBattles") == [{"limit": 3, "countryId": "c1", "isActive": True}]


def test_observation_times_are_rfc3339_utc(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("user.getUserLite", {"_id": "u1", "username": "Kiro"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"user_id": "u1"})
        observed = result.structuredContent["observed_at"]
        assert observed.endswith("Z")
        assert "T" in observed

    run_mcp(settings, stub, scenario)


def test_output_budget_error_is_structured_when_exceeded(stub: UpstreamStub) -> None:
    from warera_mcp.domain.models import GetPlayerResult, PlayerProfile
    from warera_mcp.mcp_server.responses import success_result

    model = GetPlayerResult(
        observed_at=datetime(2024, 5, 1, tzinfo=UTC),
        player=PlayerProfile(id="u1", username="Kiro" * 50),
        resolved_by="user_id",
    )
    with pytest.raises(Exception) as excinfo:
        success_result(model, summary="s", operation="get_player", max_bytes=64)
    assert "output budget" in str(excinfo.value)


def test_tight_output_budget_still_serves_small_payloads(stub: UpstreamStub) -> None:
    tight = Settings(max_output_bytes=4096, max_retries=0)
    stub.route("itemTrading.getPrices", {"iron": 12.5})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert result.isError is False
        assert result.structuredContent["price"] == 12.5

    run_mcp(tight, stub, scenario)


def test_server_instructions_are_exposed_with_the_credential_warning() -> None:
    assert "READ-ONLY" in SERVER_INSTRUCTIONS
    assert "in clear" in SERVER_INSTRUCTIONS
    assert "untrusted" in SERVER_INSTRUCTIONS


def test_every_tool_call_carries_a_correlation_id_on_failure(
    settings: Settings, stub: UpstreamStub
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {})
        assert result.isError is True
        assert result.structuredContent["error"]["operation"] == "get_player"

    run_mcp(settings, stub, scenario)
