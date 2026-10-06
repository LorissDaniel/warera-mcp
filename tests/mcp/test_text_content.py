"""Regression coverage for clients that consume content without structuredContent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.types import TextContent

from tests.conftest import UpstreamStub, run_mcp
from warera_mcp.config import Settings


def text_payload(result: Any) -> dict[str, Any]:
    """Read the JSON facts using only the MCP text content blocks."""
    blocks = [block.text for block in result.content if isinstance(block, TextContent)]
    assert len(blocks) == 2
    payload: dict[str, Any] = json.loads(blocks[1])
    return payload


@pytest.mark.parametrize(("increment_value", "sixth_cost"), [(50, 300), (60, 360), (0, 0)])
def test_cement_and_company_rules_are_available_to_text_only_clients(
    settings: Settings, stub: UpstreamStub, increment_value: int, sixth_cost: int
) -> None:
    fixture = Path(__file__).resolve().parents[1] / "contract/fixtures/game_configuration.json"
    config = json.loads(fixture.read_text())["entries"][0]["data"]
    config["company"]["constructionCostIncreasePerCompany"] = increment_value
    stub.route("gameConfig.getGameConfig", config)

    async def scenario(session: Any) -> None:
        cement = await session.call_tool("get_item_details", {"item_code": "concrete"})
        assert not cement.isError
        facts = text_payload(cement)
        assert facts["item"]["production"] == {
            "production_points_per_unit": 10,
            "inputs": [{"item_code": "limestone", "quantity": 10}],
        }
        assert facts["provenance"]["source"] == "official_game_configuration"
        assert facts == cement.structuredContent

        rules = await session.call_tool("get_game_rules", {"topic": "companies"})
        assert not rules.isError
        facts = text_payload(rules)
        increment = next(
            row for row in facts["records"] if row["id"] == "companies.construction_cost_increment"
        )
        assert increment["value"] == increment_value
        assert increment["unit"] == "concrete_units"
        assert increment["cost_item_code"] == "concrete"
        assert increment["company_count_scope"] == "all_owned_companies"
        assert increment["formula"] == "concrete_cost = value * (owned_company_count + 1)"
        # The sixth company requires five already owned; no player lookup is needed.
        assert increment["value"] * (5 + increment["owned_company_count_offset"]) == sixth_cost
        assert facts == rules.structuredContent

        items = await session.call_tool("get_game_rules", {"topic": "items", "limit": 1})
        assert not items.isError
        facts = text_payload(items)
        assert len(facts["records"]) == 1
        assert facts["has_more"] is True
        assert facts["total_count"] > 1
        assert facts == items.structuredContent

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("gameConfig.getGameConfig")) == 1
    assert not stub.calls("user.getUserLite")


def test_text_clients_receive_partial_results_and_warnings(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route("user.getUserLite", {"_id": "u1", "username": "Città 🏭"})
    stub.route_status("user.getUserById", 503)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"user_id": "u1"})
        assert not result.isError
        facts = text_payload(result)
        assert facts["player"]["username"] == "Città 🏭"
        assert facts["partial"] is True
        assert facts["warnings"]
        assert facts == result.structuredContent

    run_mcp(settings, stub, scenario)
