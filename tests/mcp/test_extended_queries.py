"""MCP regression scenarios for filtered pages, deeper books and full-profile facts."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from tests.mcp.test_tool_calls import seed_public_routes
from warera_mcp.config import Settings
from warera_mcp.domain.normalization import normalize_player_lite


def test_work_filters_and_cursor_are_applied_upstream(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route(
        "workOffer.getWorkOffersPaginated",
        {
            "items": [
                {
                    "_id": "w1",
                    "user": "u2",
                    "company": "co1",
                    "region": "r2",
                    "quantity": 2,
                    "initialQuantity": 10,
                    "wage": 5,
                    "wageAfterTax": 0,
                    "createdAt": "2026-10-05T09:00:00Z",
                }
            ],
            "nextCursor": "work-page-2",
        },
    )

    async def scenario(session: Any) -> None:
        arguments = {
            "item_code": "iron",
            "region_id": "r2",
            "citizenship": "c1",
            "user_id": "u2",
            "level": 0,
            "energy": 0,
            "production": 0,
            "limit": 2,
            "minimum_net_wage": 1,
        }
        first = await session.call_tool("get_work_market", arguments)
        assert first.isError is False
        assert first.structuredContent["offers"] == []  # Net zero must not fall back to gross.
        assert first.structuredContent["page"] == {
            "has_more": True,
            "next_cursor": "work-page-2",
        }
        stub.route(
            "workOffer.getWorkOffersPaginated",
            {
                "items": [
                    {
                        "_id": "w2",
                        "user": "u2",
                        "company": "co1",
                        "region": "r2",
                        "quantity": 2,
                        "initialQuantity": 10,
                        "wage": 5,
                        "createdAt": "2026-10-05T09:00:00Z",
                    }
                ],
            },
        )
        second = await session.call_tool(
            "get_work_market",
            {
                **arguments,
                "cursor": first.structuredContent["page"]["next_cursor"],
            },
        )
        assert second.isError is False
        offer = second.structuredContent["offers"][0]
        assert offer["id"] == "w2" and offer["user_id"] == "u2"
        assert offer["initial_quantity"] == 10 and offer["quantity"] == 2
        assert offer["created_at"] == "2026-10-05T09:00:00Z"
        assert second.structuredContent["page"]["has_more"] is False

    run_mcp(settings, stub, scenario)
    inputs = stub.inputs("workOffer.getWorkOffersPaginated")
    expected = {
        "regionId": "r2",
        "citizenship": "c1",
        "userId": "u2",
        "level": 0,
        "energy": 0,
        "production": 0,
        "limit": 2,
    }
    assert inputs == [expected, {**expected, "cursor": "work-page-2"}]
    assert len(stub.calls("workOffer.getWageStats")) == 1  # Shared benchmark, distinct pages.
    assert all(
        "authorization" not in h for h in stub.headers_for("workOffer.getWorkOffersPaginated")
    )


def test_work_offer_failure_is_not_an_empty_complete_page(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route_status("workOffer.getWorkOffersPaginated", 503)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_work_market", {"item_code": "iron"})
        assert result.isError is False
        data = result.structuredContent
        assert data["partial"] is True and data["offers"] == []
        assert "page" not in data and "wage_stats" in data

    run_mcp(settings, stub, scenario)


def test_deeper_book_uses_api_limit_and_does_not_claim_full_fill(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route(
        "tradingOrder.getTopOrders",
        {
            "sellOrders": [{"price": p, "quantity": 1} for p in range(30, 0, -1)],
            "buyOrders": [{"price": 0.5, "quantity": 1}],
        },
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "search_market",
            {
                "item_code": "iron",
                "side": "sell",
                "max_orders": 25,
                "depth_quantity": 40,
            },
        )
        assert result.isError is False
        data = result.structuredContent
        assert [row["price"] for row in data["sell_orders"]] == list(range(1, 26))
        assert data["buy_orders"] == [] and "best_bid" not in data
        assert data["depth"]["quantity"] == 25
        assert data["depth"]["estimated_vwap"] == 13
        # Changing requested depth must not reuse the shorter upstream book.
        larger = await session.call_tool(
            "search_market",
            {
                "item_code": "iron",
                "max_orders": 100,
            },
        )
        assert len(larger.structuredContent["sell_orders"]) == 30

    run_mcp(settings, stub, scenario)
    assert stub.inputs("tradingOrder.getTopOrders") == [
        {"itemCode": "iron", "limit": 25},
        {"itemCode": "iron", "limit": 100},
    ]


def test_event_filters_are_canonicalized_before_pagination(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "event.getEventsPaginated",
        {
            "items": [
                {
                    "_id": "e1",
                    "countries": ["c1"],
                    "data": {
                        "type": "battleOpened",
                        "battle": "b1",
                        "attackerCountry": "c1",
                        "defenderRegion": "r2",
                    },
                }
            ],
            "nextCursor": "events-2",
        },
    )

    async def scenario(session: Any) -> None:
        arguments = {
            "country_id": "c1",
            "event_types": ["BATTLEOPENED", "battleOpened"],
            "limit": 2,
        }
        first = await session.call_tool("search_events", arguments)
        assert first.isError is False
        event = first.structuredContent["events"][0]
        assert event["type"] == "battleopened"
        assert set(event["related_ids"]) == {"c1", "b1", "r2"}
        await session.call_tool("search_events", {**arguments, "cursor": "events-2"})

    run_mcp(settings, stub, scenario)
    expected = {"countryId": "c1", "eventTypes": ["battleOpened"], "limit": 2}
    assert stub.inputs("event.getEventsPaginated") == [expected, {**expected, "cursor": "events-2"}]


def test_unknown_event_filter_is_actionable_and_does_not_reach_api(
    settings: Settings, stub: UpstreamStub
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool("search_events", {"event_types": ["war"]})
        assert error_code(result) == "INVALID_INPUT"
        assert "warDeclared" in result.structuredContent["error"]["message"]

    run_mcp(settings, stub, scenario)
    assert not stub.requests


def test_war_and_defender_region_filters_continue_with_same_direction(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)

    async def scenario(session: Any) -> None:
        args = {
            "war_id": "war1",
            "defender_region_id": "r2",
            "country_id": "c1",
            "is_active": False,
            "direction": "backward",
            "limit": 2,
        }
        result = await session.call_tool("search_battles", args)
        assert result.isError is False
        assert not any("no country" in w for w in result.structuredContent["warnings"])
        await session.call_tool("search_battles", {**args, "cursor": "cursor-b"})

    run_mcp(settings, stub, scenario)
    expected = {
        "warId": "war1",
        "defenderRegionId": "r2",
        "countryId": "c1",
        "isActive": False,
        "direction": "backward",
        "limit": 2,
    }
    assert stub.inputs("battle.getBattles") == [expected, {**expected, "cursor": "cursor-b"}]


def test_companies_preserve_local_and_upstream_continuation_without_false_total(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route("company.getCompanies", {"items": ["co1", "co2"], "nextCursor": "companies-2"})

    async def scenario(session: Any) -> None:
        args = {"user_id": "u1", "limit": 1, "include_bonus": False}
        first = await session.call_tool("get_player_companies", args)
        data = first.structuredContent
        assert first.isError is False
        assert data["next_offset"] == 1 and data["has_more"] is True
        assert "total_count" not in data and data["page"]["next_cursor"] == "companies-2"
        last_on_page = await session.call_tool("get_player_companies", {**args, "offset": 1})
        assert "next_offset" not in last_on_page.structuredContent
        assert last_on_page.structuredContent["has_more"] is True
        stub.route("company.getCompanies", {"items": ["co3"]})
        next_page = await session.call_tool(
            "get_player_companies",
            {
                **args,
                "cursor": "companies-2",
                "offset": 0,
            },
        )
        assert next_page.isError is False
        assert next_page.structuredContent["has_more"] is False
        assert "total_count" not in next_page.structuredContent  # Last page count isn't total.

    run_mcp(settings, stub, scenario)
    assert stub.inputs("company.getCompanies") == [
        {"userId": "u1", "perPage": 100},
        {"userId": "u1", "perPage": 100, "cursor": "companies-2"},
    ]


FULL_PROFILE = {
    "_id": "u1",
    "username": "Kiro",
    "region": "r1",
    "location": "r2",
    "party": "p1",
    "company": "co1",
    "muMaxLevelRewarded": 0,
    "updatedAt": "2026-10-05T10:00:00Z",
    "equipment": {"weapon": "eq1", "chest": "eq2"},
    "missions": {
        "claimedCount": 160,
        "totalXpClaimed": 7870,
        "rerolledDailyMissions": 0,
        "claimedAt": {"daily": "2026-10-05T09:02:26Z"},
    },
    "finishedTours": {"onboarding": False},
    "preferences": {"secret": "omit-me"},
    "emailVerified": True,
    "availableColorSchemes": ["red"],
    "__v": 9,
}


def test_full_profile_adds_linked_facts_without_ui_or_account_metadata(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route("user.getUserById", FULL_PROFILE)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Kiro"})
        assert result.isError is False
        data = result.structuredContent
        assert data["profile_source"] == "full" and data["partial"] is False
        player = data["player"]
        assert player["country_id"] == "c1"  # Lite facts survive absent full fields.
        assert player["location_id"] == "r2" and player["company_id"] == "co1"
        assert player["party_id"] == "p1" and player["military_unit_max_level_rewarded"] == 0
        assert player["equipment_ids"] == {"weapon": "eq1", "chest": "eq2"}
        assert player["mission_statistics"]["claimedCount"] == 160
        assert player["mission_statistics"]["rerolledDailyMissions"] == 0
        assert player["mission_claimed_at"]["daily"] == "2026-10-05T09:02:26Z"
        assert player["finished_tours"] == {"onboarding": False}
        assert not (
            {"preferences", "emailVerified", "availableColorSchemes", "__v"} & player.keys()
        )
        assert "omit-me" not in str(result)
        focused = await session.call_tool(
            "get_player",
            {
                "user_id": "u1",
                "fields": ["missions", "equipment"],
            },
        )
        assert set(focused.structuredContent["player"]) == {
            "id",
            "username",
            "mission_statistics",
            "mission_claimed_at",
            "finished_tours",
            "equipment_ids",
        }

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("user.getUserById")) == 1
    assert all("authorization" not in h for h in stub.headers_for("user.getUserById"))


@pytest.mark.parametrize("full_data", [{"_id": "wrong", "region": "wrong-region"}, {}, None])
def test_malformed_full_identity_retains_lite_facts(
    settings: Settings, stub: UpstreamStub, full_data: Any
) -> None:
    seed_public_routes(stub)
    stub.route("user.getUserById", full_data)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"user_id": "u1"})
        data = result.structuredContent
        assert result.isError is False
        assert data["profile_source"] == "lite" and data["partial"] is True
        assert data["player"]["region_id"] == "r1"

    run_mcp(settings, stub, scenario)


def test_full_profile_failure_is_partial_and_lite_mode_skips_it(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_public_routes(stub)
    stub.route_status("user.getUserById", 503)

    async def scenario(session: Any) -> None:
        full = await session.call_tool("get_player", {"user_id": "u1"})
        assert full.isError is False
        assert full.structuredContent["partial"] is True
        assert full.structuredContent["player"]["skills"]["production"] == 3.5
        lite = await session.call_tool(
            "get_player",
            {
                "user_id": "u1",
                "include_full_profile": False,
            },
        )
        assert lite.structuredContent["partial"] is False
        assert lite.structuredContent["profile_source"] == "lite"
        await session.call_tool("get_player", {"user_id": "u1", "fields": ["level"]})

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("user.getUserById")) == 1


def test_profile_extra_maps_are_bounded_and_invalid_dates_remain_unknown() -> None:
    profile, warnings = normalize_player_lite(
        {
            **FULL_PROFILE,
            "equipment": {f"slot{i}": f"e{i}" for i in range(80)},
            "missions": {"claimedAt": {"daily": "bad-date"}},
            "finishedTours": {"onboarding": False},
        }
    )
    assert profile.equipment_ids and len(profile.equipment_ids) == 64
    assert profile.mission_claimed_at is None and profile.mission_statistics is None
    assert profile.finished_tours == {"onboarding": False}
    assert any("truncated" in w for w in warnings)
    assert any("timestamp" in w for w in warnings)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_work_market", {"item_code": "iron", "energy": -1}),
        ("get_work_market", {"item_code": "iron", "production": -1}),
        ("get_work_market", {"item_code": "iron", "level": -1}),
        ("get_work_market", {"item_code": "iron", "cursor": "bad\n"}),
        ("search_battles", {"direction": "latest"}),
        ("search_battles", {"war_id": "bad\n"}),
        ("search_battles", {"defender_region_id": "bad\n"}),
        ("get_player_companies", {"user_id": "u1", "cursor": "bad\n"}),
    ],
)
def test_extended_inputs_reject_invalid_values_before_network(
    settings: Settings, stub: UpstreamStub, tool: str, arguments: dict[str, Any]
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, arguments)
        assert result.isError is True

    run_mcp(settings, stub, scenario)
    assert not stub.requests


@pytest.mark.parametrize(
    ("tool", "procedure", "list_key", "arguments"),
    [
        ("search_events", "event.getEventsPaginated", "events", {"limit": 1}),
        ("search_battles", "battle.getBattles", "battles", {"limit": 1}),
        (
            "get_work_market",
            "workOffer.getWorkOffersPaginated",
            "offers",
            {"item_code": "iron", "limit": 1},
        ),
    ],
)
def test_upstream_ignoring_limit_is_bounded_and_marked_partial(
    settings: Settings,
    stub: UpstreamStub,
    tool: str,
    procedure: str,
    list_key: str,
    arguments: dict[str, Any],
) -> None:
    seed_public_routes(stub)
    stub.route(procedure, {"items": [{"_id": "1"}, {"_id": "2"}], "nextCursor": "next"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, arguments)
        assert result.isError is False
        assert len(result.structuredContent[list_key]) == 1
        assert result.structuredContent["partial"] is True
        assert result.structuredContent["page"]["next_cursor"] == "next"
        assert any("omitted" in w for w in result.structuredContent["warnings"])
        if tool == "search_battles":
            assert "danni ?-?" in result.content[0].text

    run_mcp(settings, stub, scenario)


def test_initial_work_quantity_does_not_invent_remaining_quantity() -> None:
    from warera_mcp.domain.normalization import normalize_work_offer

    offer = normalize_work_offer({"initialQuantity": 10})
    assert offer.initial_quantity == 10 and offer.quantity is None
