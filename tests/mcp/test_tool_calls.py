"""End-to-end tool calls through a real MCP client session against a stubbed host.

Every tool in the approved inventory is invoked at least once, plus the failure
modes that matter for model-facing usefulness: ambiguity, missing identifier,
unknown item, invalid bounds and partial composite results.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from warera_mcp.config import Settings


def seed_public_routes(stub: UpstreamStub) -> None:
    stub.route(
        "user.getUserLite",
        {
            "_id": "u1",
            "username": "Kiro",
            "leveling": {"level": 12},
            "country": "c1",
            "region": "r1",
        },
    )
    stub.route("search.searchUsers", ["u1"])
    stub.route("itemTrading.getPrices", {"iron": 12.5, "bread": 3.25})
    stub.route(
        "tradingOrder.getTopOrders",
        {
            "buyOrders": [{"price": 10, "quantity": 5}, {"price": 11, "quantity": 2}],
            "sellOrders": [{"price": 15, "quantity": 3}, {"price": 16, "quantity": 4}],
        },
    )
    stub.route(
        "country.getAllCountries",
        [
            {"_id": "c1", "name": "Italy", "code": "IT", "warsWith": ["c2"]},
            {"_id": "c2", "name": "Gaul", "code": "GA", "warsWith": ["c1"]},
        ],
    )
    stub.route(
        "country.getCountryById",
        {"_id": "c1", "name": "Italy", "code": "IT", "warsWith": ["c2"]},
    )
    stub.route(
        "region.getRegionsObject",
        {
            "r1": {"_id": "r1", "name": "Lazio", "country": "c1", "population": 500},
            "r2": {"_id": "r2", "name": "Lazio", "country": "c2", "population": 400},
        },
    )
    stub.route(
        "region.getById",
        {"_id": "r1", "name": "Lazio", "country": "c1", "population": 500, "development": 9.1},
    )
    stub.route("company.getCompanies", {"items": ["co1", "co2"]})
    stub.route(
        "company.getById",
        {
            "_id": "co1",
            "name": "Iron Works",
            "itemCode": "iron",
            "production": 250,
            "workerCount": 4,
            "user": "u1",
            "region": "r1",
        },
    )
    stub.route(
        "company.getProductionBonus", {"strategicBonus": 11, "depositBonus": 30, "total": 41}
    )
    stub.route(
        "workOffer.getWageStats",
        {"allowedRange": {"min": 1, "max": 9, "average": 4}, "topOffer": {"wage": 9}},
    )
    stub.route(
        "workOffer.getWorkOffersPaginated",
        {
            "items": [
                {
                    "company": "co1",
                    "region": "r1",
                    "quantity": 2,
                    "wage": 9,
                    "wageAfterTax": 8,
                    "text": "join <script>bad()</script> now",
                }
            ],
            "nextCursor": "cursor-1",
        },
    )
    stub.route(
        "battle.getBattles",
        {
            "items": [
                {
                    "_id": "b1",
                    "type": "land",
                    "isActive": True,
                    "attacker": {"country": "c1", "damages": 100},
                    "defender": {"country": "c2", "damages": 90},
                }
            ],
            "nextCursor": "cursor-b",
        },
    )
    stub.route(
        "battle.getById",
        {
            "_id": "b1",
            "type": "land",
            "isActive": True,
            "attacker": {"country": "c1", "damages": 100, "wonRounds": 2},
            "defender": {"country": "c2", "damages": 90, "wonRounds": 1},
            "roundsToWin": 3,
        },
    )
    stub.route(
        "battle.getLiveBattleData",
        {
            "battle": "b1",
            "round": {
                "_id": "rnd1",
                "attackerDamages": 5,
                "defenderDamages": 4,
                "points": {"attacker": 2, "defender": 1},
                "nextTickAt": "2024-05-01T10:05:00Z",
            },
        },
    )
    stub.route(
        "battleRanking.getRanking",
        {
            "itemCount": 4,
            "items": [
                {"user": "u1", "rank": 1, "value": 100},
                {"user": "u2", "rank": 2, "value": 90},
                {"user": "u3", "rank": 3, "value": 80},
            ],
        },
    )
    stub.route(
        "event.getEventsPaginated",
        {
            "items": [
                {
                    "_id": "e1",
                    "type": "war",
                    "createdAt": "2024-05-01T09:00:00Z",
                    "countries": ["c1"],
                    "data": {"text": "<b>War</b> declared"},
                }
            ],
            "nextCursor": "cursor-e",
        },
    )


def test_get_player_by_id(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"user_id": "u1"})
        assert result.isError is False
        assert result.structuredContent["player"]["username"] == "Kiro"
        assert result.structuredContent["resolved_by"] == "user_id"
        assert result.structuredContent["observed_at"].endswith("Z")

    run_mcp(settings, stub, scenario)
    assert stub.methods == {"GET"}


def test_get_player_projects_requested_fields(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player", {"user_id": "u1", "fields": ["profile", "level"]}
        )
        player = result.structuredContent["player"]
        assert player["username"] == "Kiro"
        assert player["level"] == 12
        assert "country_id" not in player
        assert "skills" not in player

    run_mcp(settings, stub, scenario)


def test_get_player_by_username_requires_an_exact_match(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Kiro"})
        assert result.isError is False
        assert result.structuredContent["resolved_by"] == "username"

    run_mcp(settings, stub, scenario)


def test_get_player_reports_ambiguity_without_guessing(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route("search.searchUsers", ["u1", "u2"])
    stub.route("user.getUserLite", {"username": "Twin"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Twin"})
        assert error_code(result) == "AMBIGUOUS_ENTITY"
        assert len(result.structuredContent["error"]["details"]["candidates"]) == 2

    run_mcp(settings, stub, scenario)


def test_get_player_reports_no_exact_match(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("search.searchUsers", ["u1"])
    stub.route("user.getUserLite", {"_id": "u1", "username": "SomeoneElse"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Kiro"})
        assert error_code(result) == "NOT_FOUND"

    run_mcp(settings, stub, scenario)


def test_get_player_requires_exactly_one_identifier(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(session: Any) -> None:
        missing = await session.call_tool("get_player", {})
        assert error_code(missing) == "INVALID_INPUT"

        both = await session.call_tool("get_player", {"user_id": "u1", "username": "Kiro"})
        assert error_code(both) == "INVALID_INPUT"

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


def test_get_player_companies_composes_details_and_bonus(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player_companies", {"user_id": "u1"})
        payload = result.structuredContent
        assert result.isError is False
        assert payload["total_count"] == 2
        assert payload["player"] == {"id": "u1", "username": "Kiro"}
        assert payload["companies"][0]["production_bonus"] == pytest.approx(0.41)

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("company.getById")) == 2


def test_get_player_companies_degrades_to_partial_when_bonus_fails(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route_status("company.getProductionBonus", 500)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player_companies", {"user_id": "u1"})
        payload = result.structuredContent
        assert result.isError is False
        assert payload["partial"] is True
        assert "production_bonus" not in payload["companies"][0]
        assert any("bonus" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_get_company_overview_includes_bonus_and_region(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_company_overview",
            {"company_id": "co1", "include_region_context": True},
        )
        payload = result.structuredContent
        assert payload["company"]["item_code"] == "iron"
        assert payload["production_bonus"]["total_fraction"] == pytest.approx(0.41)
        assert payload["region"]["id"] == "r1"

    run_mcp(settings, stub, scenario)


def test_get_country_overview_with_regions(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_country_overview", {"country_id": "c1", "include_regions": True}
        )
        payload = result.structuredContent
        assert payload["country"]["name"] == "Italy"
        assert [region["id"] for region in payload["regions"]] == ["r1"]

    run_mcp(settings, stub, scenario)


def test_get_country_overview_by_name_is_exact(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        ok = await session.call_tool("get_country_overview", {"country_name": "italy"})
        assert ok.structuredContent["country"]["id"] == "c1"

        missing = await session.call_tool("get_country_overview", {"country_name": "Atlantis"})
        assert error_code(missing) == "NOT_FOUND"

    run_mcp(settings, stub, scenario)


def test_get_region_by_name_rejects_ambiguity(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_region", {"region_name": "Lazio"})
        assert error_code(result) == "AMBIGUOUS_ENTITY"

    run_mcp(settings, stub, scenario)


def test_get_region_by_id_includes_country(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_region", {"region_id": "r1"})
        payload = result.structuredContent
        assert payload["region"]["name"] == "Lazio"
        assert payload["country"]["name"] == "Italy"

    run_mcp(settings, stub, scenario)


def test_get_country_wars_lists_opponents_and_battles(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_country_wars", {"country_id": "c1"})
        payload = result.structuredContent
        assert payload["opponents"] == [{"country_id": "c2", "name": "Gaul"}]
        assert payload["active_battles"][0]["id"] == "b1"
        assert payload["active_battles"][0]["attacker"]["country_name"] == "Italy"
        assert payload["active_battles"][0]["defender"]["country_name"] == "Gaul"
        assert payload["partial"] is False

    run_mcp(settings, stub, scenario)


def test_get_country_wars_is_partial_when_battles_fail(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route_status("battle.getBattles", 503)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_country_wars", {"country_id": "c1"})
        payload = result.structuredContent
        assert result.isError is False
        assert payload["partial"] is True
        assert payload["active_battles"] == []

    run_mcp(settings, stub, scenario)


def test_get_market_price_and_unknown_item(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        ok = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert ok.structuredContent["price"] == 12.5
        assert ok.structuredContent["source"] == "global_price"
        assert ok.structuredContent["freshness_seconds"] >= 0

        unknown = await session.call_tool("get_market_price", {"item_code": "unobtainium"})
        assert error_code(unknown) == "NOT_FOUND"

    run_mcp(settings, stub, scenario)


def test_search_market_reports_the_visible_book(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("search_market", {"item_code": "iron", "side": "both"})
        payload = result.structuredContent
        assert payload["best_bid"] == 11.0
        assert payload["best_ask"] == 15.0
        assert payload["spread"] == pytest.approx(4.0)
        assert [level["price"] for level in payload["buy_orders"]] == [11.0, 10.0]
        assert [level["price"] for level in payload["sell_orders"]] == [15.0, 16.0]
        assert any("not a full-market average" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_search_market_estimates_depth_for_one_side(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "search_market",
            {"item_code": "iron", "side": "sell", "depth_quantity": 4},
        )
        payload = result.structuredContent
        assert payload["best_ask"] == 15.0
        # A single-side view has no bid, so no spread can be derived.
        assert "best_bid" not in payload
        assert "spread" not in payload
        assert payload["depth"]["quantity"] == pytest.approx(4.0)
        assert payload["depth"]["estimated_vwap"] == pytest.approx(15.25)

    run_mcp(settings, stub, scenario)


def test_search_market_depth_requires_a_single_side(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "search_market", {"item_code": "iron", "side": "both", "depth_quantity": 4}
        )
        payload = result.structuredContent
        assert "depth" not in payload
        assert any("single side" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_get_work_market_filters_locally(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_work_market", {"item_code": "iron", "minimum_net_wage": 1000}
        )
        payload = result.structuredContent
        assert payload["offers"] == []
        assert any("locally" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_search_battles_reports_unfiltered_page(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("search_battles", {"limit": 5})
        payload = result.structuredContent
        assert payload["battles"][0]["id"] == "b1"
        assert payload["battles"][0]["attacker"]["country_id"] == "c1"
        assert payload["battles"][0]["attacker"]["country_name"] == "Italy"
        assert payload["battles"][0]["defender"]["country_name"] == "Gaul"
        assert payload["page"]["has_more"] is True
        text = result.content[0].text
        assert "Italy vs Gaul" in text
        assert "danni 100-90" in text
        assert "next_cursor=" in text
        assert any("ordering is not guaranteed" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_search_battles_keeps_ids_when_country_name_lookup_fails(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route_status("country.getAllCountries", 500)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("search_battles", {"limit": 5})
        payload = result.structuredContent
        assert result.isError is False
        assert payload["battles"][0]["attacker"]["country_id"] == "c1"
        assert "country_name" not in payload["battles"][0]["attacker"]
        assert any("country ids are returned" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_get_battle_combines_dossier_and_live(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle", {"battle_id": "b1"})
        payload = result.structuredContent
        assert payload["battle"]["rounds_to_win"] == 3
        assert payload["battle"]["attacker"]["country_name"] == "Italy"
        assert payload["battle"]["defender"]["country_name"] == "Gaul"
        assert "Italy vs Gaul" in result.content[0].text
        assert payload["live_status"]["round_id"] == "rnd1"
        assert payload["live_status"]["next_tick_at"].endswith("Z")
        assert payload["partial"] is False

    run_mcp(settings, stub, scenario)


def test_get_battle_returns_partial_when_live_data_fails(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route_status("battle.getLiveBattleData", 500)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle", {"battle_id": "b1"})
        payload = result.structuredContent
        assert result.isError is False
        assert payload["partial"] is True
        assert payload["battle"]["id"] == "b1"
        assert "live_status" not in payload

    run_mcp(settings, stub, scenario)


def test_get_battle_ranking_is_truncated_locally(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle_ranking", {"battle_id": "b1", "limit": 2})
        payload = result.structuredContent
        assert [entry["rank"] for entry in payload["entries"]] == [1, 2]
        assert payload["item_count"] == 4
        assert payload["truncated"] is True
        assert payload["entries"][0]["name"] == "Kiro"

    run_mcp(settings, stub, scenario)


def test_search_events_sanitizes_text_and_pages(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("search_events", {"country_id": "c1"})
        payload = result.structuredContent
        assert payload["events"][0]["summary"] == "War declared"
        assert payload["page"]["next_cursor"] == "cursor-e"
        assert any("untrusted" in warning for warning in payload["warnings"])

    run_mcp(settings, stub, scenario)


def test_cursor_is_forwarded_to_the_upstream(settings: Settings, stub: UpstreamStub) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        await session.call_tool("search_events", {"limit": 3, "cursor": "opaque-cursor"})

    run_mcp(settings, stub, scenario)
    assert stub.inputs("event.getEventsPaginated") == [{"limit": 3, "cursor": "opaque-cursor"}]


def test_no_credential_input_is_required_by_any_v1_tool(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        tools = await session.list_tools()
        for tool in tools.tools:
            assert "player_context" not in tool.inputSchema.get("properties", {}), tool.name

        result = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert result.isError is False

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_market", {"item_code": "iron", "side": "sideways"}),
        ("search_market", {"item_code": "iron", "max_orders": 0}),
        ("search_market", {"item_code": "iron", "max_orders": 99}),
        ("search_events", {"limit": 0}),
        ("search_events", {"limit": 500}),
        ("get_battle_ranking", {"battle_id": "b1", "entity_type": "guild"}),
        ("get_player_companies", {"user_id": "u1", "limit": 0}),
        ("get_player_companies", {"user_id": "u1", "offset": -1}),
        ("search_battles", {"limit": 11}),
        ("get_battle", {"battle_id": ""}),
    ],
)
def test_invalid_arguments_are_rejected_before_any_upstream_call(
    settings: Settings, stub: UpstreamStub, tool: str, arguments: dict[str, Any]
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, arguments)
        assert result.isError is True, f"{tool} accepted {arguments}"

    run_mcp(settings, stub, scenario)
    assert stub.requests == [], f"{tool} must not reach the upstream with invalid input"
