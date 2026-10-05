from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import UpstreamStub
from warera_mcp.application import Services

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "contract" / "fixtures"


@pytest.mark.asyncio
async def test_official_snapshot_drives_skill_item_upgrade_and_schedule_projections(
    services: Services, stub: UpstreamStub
) -> None:
    config_document = json.loads((FIXTURE_ROOT / "game_configuration.json").read_text())
    dates_document = json.loads((FIXTURE_ROOT / "game_dates.json").read_text())
    config = config_document["entries"][0]["data"]
    dates = dates_document["entries"][0]["data"]
    stub.route("gameConfig.getGameConfig", config)
    stub.route("gameConfig.getDates", dates)

    skill = await services.game_configuration.get_skill("production", 0, 10, 0, 50)
    levels = {row["level"]: row for row in skill.levels}
    assert levels[0]["total_cost"] == 0
    assert "cost" not in levels[0]
    assert levels[1]["cost"] == 1
    assert levels[10]["total_cost"] == 55
    assert levels[0]["is_a_bar"] is True
    assert "is_a_bar" not in levels[1]

    item = await services.game_configuration.get_item("bread")
    assert item.item["production"]["inputs"] == [{"item_code": "grain", "quantity": 10}]
    assert item.item["effects"]["health_regen_fraction"] == 0.1

    upgrades = await services.game_configuration.get_rules("upgrades", 0, 50)
    assert upgrades.records
    assert upgrades.records[0]["steel_cost"] >= 0

    schedule = await services.game_configuration.get_schedule()
    assert schedule.schedule["next_day_at"].tzinfo is not None
    assert schedule.provenance.freshness_seconds >= 0
    assert len(stub.calls("gameConfig.getGameConfig")) == 1
    assert stub.methods == {"GET"}
    for headers in stub.headers_for("gameConfig.getGameConfig"):
        assert "api-key" not in headers
        assert "authorization" not in headers


async def test_all_official_raw_materials_expose_points_without_recipes(
    services: Services, stub: UpstreamStub
) -> None:
    document = json.loads((FIXTURE_ROOT / "game_configuration.json").read_text())
    config = document["entries"][0]["data"]
    stub.route("gameConfig.getGameConfig", config)
    raw_items = {code: item for code, item in config["items"].items() if item["type"] == "raw"}
    assert {"fish", "livestock", "iron"} <= raw_items.keys()
    for code, raw in raw_items.items():
        result = await services.game_configuration.get_item(code)
        assert result.item["production"] == {
            "production_points_per_unit": raw["productionPoints"]
        }
        assert result.partial is False
        assert result.warnings == []
        recipe, _ = await services.game_configuration.get_recipe(code)
        assert recipe is None


@pytest.mark.parametrize("points", [None, True, -1, "40"])
async def test_malformed_raw_points_are_not_reported_as_recipe_problems(
    services: Services, stub: UpstreamStub, points: object
) -> None:
    stub.route("gameConfig.getGameConfig", {
        "items": {"fish": {"type": "raw", "productionPoints": points}}
    })
    result = await services.game_configuration.get_item("fish")
    assert "production" not in result.item
    assert result.partial is True
    assert result.warnings == ["production points were malformed"]


async def test_product_missing_inputs_retains_points_but_has_no_validated_recipe(
    services: Services, stub: UpstreamStub
) -> None:
    stub.route("gameConfig.getGameConfig", {
        "items": {"steel": {"type": "product", "productionPoints": 10}}
    })
    result = await services.game_configuration.get_item("steel")
    assert result.item["production"] == {"production_points_per_unit": 10}
    assert result.partial is True
    assert result.warnings == ["production recipe was incomplete or malformed"]
    recipe, _ = await services.game_configuration.get_recipe("steel")
    assert recipe is None
