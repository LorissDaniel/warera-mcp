"""Regression coverage for rich, linkable public facts without inferred calculations."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from tests.mcp.test_military_units import MU
from warera_mcp.config import Settings
from warera_mcp.domain.normalization import (
    normalize_battle_detail,
    normalize_battle_summary,
    normalize_live_battle,
    normalize_player_lite,
)

PLAYER = {
    "_id": "u1",
    "username": "Loris",
    "country": "c1",
    "mu": "mu1",
    "isActive": True,
    "militaryRank": 21,
    "createdAt": "2026-09-25T13:15:04Z",
    "leveling": {"level": 18, "xp": 9940, "availableSkillPoints": 0, "prestige": 0},
    "stats": {"damagesCount": 51095},
    "skills": {
        "attack": {
            "level": 0,
            "value": 100,
            "weapon": 30,
            "total": 138,
            "militaryRankPercent": 6.25,
            "buffsPercent": 0,
        },
        "armor": {"value": None, "equipment": 8, "total": 8, "totalAfterSoftCap": 17},
        "production": {
            "level": 7,
            "currentBarValue": 10,
            "total": 31,
            "hourlyBarRegen": 3.1,
            "futureNumericComponent": 12,
        },
    },
    "rankings": {"userDamages": {"value": 51095, "rank": 13386, "tier": "bronze"}},
    "dates": {
        "lastWorkAt": "2026-10-05T10:23:00Z",
        "lastWorkOfferApplications": ["2026-10-01T08:19:52Z"],
        "lastHiresAt": [],
    },
    "avatarUrl": "https://example.invalid/tracker",
    "__v": 10,
}


def test_rich_profile_and_projection(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("user.getUserLite", PLAYER)

    async def scenario(session: Any) -> None:
        response = await session.call_tool("get_player", {"user_id": "u1"})
        assert response.isError is False
        player = response.structuredContent["player"]
        assert player["military_unit_id"] == "mu1" and player["military_rank"] == 21
        assert player["leveling"]["availableSkillPoints"] == 0
        assert player["stats"]["damagesCount"] == 51095
        assert player["skills"]["attack"] == 138
        assert player["skills"]["armor"] == 8  # preserve reported total, no soft-cap inference
        assert player["skill_details"]["armor"]["total_after_soft_cap"] == 17
        assert "value" not in player["skill_details"]["armor"]
        assert player["skill_details"]["attack"]["modifiers_fraction"] == {
            "militaryRank": 0.0625,
            "buffs": 0,
        }
        assert player["skill_details"]["production"]["additional_numeric_components"] == {
            "futureNumericComponent": 12,
        }
        assert player["ranking_details"]["userDamages"] == {
            "value": 51095,
            "rank": 13386,
            "tier": "bronze",
        }
        assert player["rankings"] == {"userDamages": 51095}
        assert player["activity_dates"]["lastWorkAt"] == "2026-10-05T10:23:00Z"
        assert player["activity_date_lists"]["lastHiresAt"] == []
        assert "avatarUrl" not in player and "__v" not in player
        minimal = await session.call_tool("get_player", {"user_id": "u1", "fields": ["profile"]})
        projected = minimal.structuredContent["player"]
        assert projected["military_unit_id"] == "mu1"
        assert "skills" not in projected and "skill_details" not in projected
        assert "activity_dates" not in projected and "stats" not in projected
        assert "leveling" not in projected and "ranking_details" not in projected

    run_mcp(settings, stub, scenario)


def test_profile_unknowns_bad_fields_and_legacy_maps() -> None:
    profile, warnings = normalize_player_lite(
        {
            "_id": "u1",
            "skills": {"attack": 10, "broken": "oops", "a\n": 1},
            "rankings": {"wealth": 100},
            "dates": {"badDate": "not-a-date"},
        }
    )
    assert profile.skills == {"attack": 10} and profile.rankings == {"wealth": 100}
    assert profile.skill_details is None and profile.ranking_details is None
    assert profile.military_unit_id is None
    assert warnings and all("oops" not in warning for warning in warnings)
    oversized, warnings = normalize_player_lite(
        {
            "_id": "u1",
            "skills": {f"s{i}": i for i in range(70)},
        }
    )
    assert len(oversized.skills) == 64
    assert any("truncated" in warning for warning in warnings)


def test_leadership_outside_roster_and_role_paging(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("mu.getById", {**MU, "roles": {"managers": ["outsider"], "commanders": ["u2"]}})

    async def scenario(session: Any) -> None:
        detail = await session.call_tool("get_military_unit", {"military_unit_id": "mu1"})
        unit = detail.structuredContent["military_unit"]
        assert unit["manager_ids"] == ["outsider"]
        assert unit["commander_ids"] == ["u2"]
        assert unit["member_count"] == 3
        roster = await session.call_tool(
            "get_military_unit_members",
            {
                "military_unit_id": "mu1",
                "include_non_members": True,
                "offset": 3,
                "limit": 1,
            },
        )
        assert roster.structuredContent["total_count"] == 4
        assert roster.structuredContent["members"] == [
            {
                "user_id": "outsider",
                "is_member": False,
                "is_owner": False,
                "is_manager": True,
                "is_commander": False,
            }
        ]
        assert roster.structuredContent["has_more"] is False

    run_mcp(settings, stub, scenario)


def test_investment_pagination(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("mu.getById", {**MU, "investedMoneyByUsers": {"u1": 1000, "former": 0}})

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_military_unit_investments",
            {
                "military_unit_id": "mu1",
                "offset": 1,
                "limit": 1,
            },
        )
        assert result.isError is False
        assert result.structuredContent["investments"] == [
            {"user_id": "former", "invested_money": 0}
        ]
        assert result.structuredContent["total_count"] == 2
        assert result.structuredContent["has_more"] is False

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize("mapping", [{"u1": -1}, {"u1": True}, {"u1": "oops"}])
def test_missing_or_invalid_investments_are_not_zero(
    settings: Settings,
    stub: UpstreamStub,
    mapping: Any,
) -> None:
    stub.route("mu.getById", {**MU, "investedMoneyByUsers": mapping})

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_military_unit_investments", {"military_unit_id": "mu1"}
        )
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    ("scope", "upstream"),
    [
        ("battle_id", "battleId"),
        ("war_id", "warId"),
        ("round_id", "roundId"),
    ],
)
def test_ranking_scopes_money_and_cursor(
    settings: Settings,
    stub: UpstreamStub,
    scope: str,
    upstream: str,
) -> None:
    stub.route(
        "battleRanking.getRanking",
        {
            "items": [{"mu": "mu1", "value": 50, "rank": 3}],
            "itemCount": 8,
            "nextCursor": "page3",
        },
    )
    stub.route("mu.getById", MU)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_battle_ranking",
            {
                scope: "scope1",
                "entity_type": "mu",
                "metric": "money",
                "cursor": "page2",
                "limit": 2,
            },
        )
        assert result.isError is False
        assert stub.inputs("battleRanking.getRanking") == [
            {
                upstream: "scope1",
                "type": "mu",
                "dataType": "money",
                "side": "merged",
                "cursor": "page2",
                "limit": 2,
            }
        ]
        data = result.structuredContent
        assert data[scope] == "scope1" and data["item_count"] == 8
        assert data["page"] == {"next_cursor": "page3", "has_more": True}
        assert data["entries"][0]["value"] == 50
        assert data["truncated"] is True

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"battle_id": "b1", "war_id": "w1"},
        {"battle_id": ""},
        {"round_id": "r1", "cursor": "x" * 513},
    ],
)
def test_ranking_invalid_scopes_never_reach_api(
    settings: Settings,
    stub: UpstreamStub,
    arguments: dict[str, Any],
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle_ranking", arguments)
        assert result.isError is True

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


def test_real_nested_rounds_and_battle_links() -> None:
    current_round = {
        "_id": "r1",
        "attacker": {"damages": 0, "points": 3},
        "defender": {"damages": 15, "points": 0},
        "lastHits": [{"weapon": "secret"}],
        "battle": "b1",
        "number": 1,
        "isActive": True,
        "live": {"ticksCount": 3, "actualTickPoints": 1, "nextTickAt": "2026-10-05T12:19:00Z"},
    }
    battle = {
        "_id": "b1",
        "war": "w1",
        "isActive": True,
        "type": "war",
        "currentRound": current_round,
        "rounds": ["r1"],
        "roundsHistory": [current_round],
        "attacker": {
            "country": "c1",
            "wonRoundsCount": 0,
            "hitCount": 5,
            "muOrders": ["mu1"],
            "countryOrders": ["c1"],
        },
        "defender": {"country": "c2"},
    }
    summary = normalize_battle_summary(battle)
    detail, _ = normalize_battle_detail(battle, include_history=True)
    live = normalize_live_battle({"round": current_round})
    assert summary.war_id == detail.war_id == "w1"
    assert detail.round_ids == ["r1"]
    assert summary.attacker.military_unit_ids_with_orders == ["mu1"]
    assert summary.attacker.won_rounds == 0
    assert detail.current_round.attacker_damages == live.attacker_damages == 0
    assert detail.current_round.attacker_points == live.attacker_points == 3
    assert live.ticks_count == 3 and live.actual_tick_points == 1
    assert live.battle_id == "b1" and live.number == 1
    assert detail.round_history[0].defender_points == 0
    assert "lastHits" not in detail.model_dump_json()
    assert "secret" not in detail.model_dump_json()


def test_absent_investments_are_explicitly_unknown(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("mu.getById", {"_id": "mu1"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_military_unit_investments", {"military_unit_id": "mu1"}
        )
        assert result.isError is False
        assert result.structuredContent["available"] is False
        assert "investments" not in result.structuredContent
        assert "total_count" not in result.structuredContent

    run_mcp(settings, stub, scenario)


def test_ranking_document_id_is_not_an_entity_id(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("battleRanking.getRanking", {"items": [{"_id": "internal-row", "value": 20}]})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle_ranking", {"battle_id": "b1"})
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"

    run_mcp(settings, stub, scenario)


def test_inconsistent_ranking_count_is_partial(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("battleRanking.getRanking", {"items": [], "itemCount": 8})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle_ranking", {"battle_id": "b1"})
        assert result.isError is False
        assert result.structuredContent["partial"] is True
        assert result.structuredContent["item_count"] == 8

    run_mcp(settings, stub, scenario)


def test_live_flat_points_and_battle_metadata_are_preserved() -> None:
    data = {
        "battle": {
            "isActive": True,
            "roundIds": ["r1"],
            "roundHistory": [],
            "attackerCountryOrders": ["c1"],
            "defenderCountryOrders": [],
        },
        "round": {
            "roundId": "r1",
            "attackerDamages": 0,
            "defenderDamages": 15,
            "attackerPoints": 11,
            "defenderPoints": 0,
            "actualTickPoints": 1,
        },
    }
    live = normalize_live_battle(data, include_history=True)
    assert live.attacker_points == 11 and live.defender_points == 0
    assert live.actual_tick_points == 1
    assert live.round_ids == ["r1"]
    assert live.attacker_country_ids_with_orders == ["c1"]
    assert live.round_history == []
    assert normalize_live_battle(data).round_history is None


def test_full_public_wealth_is_preserved_and_selectable(
    settings: Settings, stub: UpstreamStub
) -> None:
    wealth = {
        "money": 0,
        "items": 12.5,
        "companies": 200,
        "equipments": 3,
        "weapons": 1,
        "total": 999,
        "futureComponent": 7,
    }
    stub.route("user.getUserLite", PLAYER)
    stub.route("user.getUserById", {**PLAYER, "stats": {"damagesCount": 51095, "wealth": wealth}})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"user_id": "u1", "fields": ["statistics"]})
        data = result.structuredContent
        assert not result.isError and data["profile_source"] == "full"
        assert data["player"]["wealth_breakdown"] == wealth
        assert any(
            "profile updated_at is not a wealth timestamp" in warning
            for warning in data["warnings"]
        )
        assert data["player"]["stats"] == {"damagesCount": 51095}
        assert "money_available" not in data["player"]
        assert "items_available" not in data["player"]
        profile = await session.call_tool("get_player", {"user_id": "u1", "fields": ["profile"]})
        assert "wealth_breakdown" not in profile.structuredContent["player"]
        assert not any(
            "wealth timestamp" in warning for warning in profile.structuredContent["warnings"]
        )
        lite = await session.call_tool(
            "get_player", {"user_id": "u1", "include_full_profile": False}
        )
        assert "wealth_breakdown" not in lite.structuredContent["player"]

    run_mcp(settings, stub, scenario)
    assert not stub.calls("inventory.getById") and not stub.calls(
        "tradingOrder.getAllOrdersByOwner"
    )
    for procedure in ("user.getUserLite", "user.getUserById"):
        assert all(
            "cookie" not in headers and "x-api-key" not in headers
            for headers in stub.headers_for(procedure)
        )


@pytest.mark.parametrize(
    "wealth", [None, {}, "invalid", {"money": None, "items": "bad", "total": True}]
)
def test_missing_or_invalid_wealth_is_not_fabricated(wealth: Any) -> None:
    profile, _ = normalize_player_lite({"_id": "u1", "stats": {"wealth": wealth}})
    assert profile.wealth_breakdown is None


def test_wealth_numeric_map_is_bounded_and_keeps_reported_zero() -> None:
    profile, warnings = normalize_player_lite(
        {"_id": "u1", "stats": {"wealth": {f"component{i}": i for i in range(70)}}}
    )
    assert len(profile.wealth_breakdown) == 64
    assert profile.wealth_breakdown["component0"] == 0
    assert any("stats.wealth" in warning and "truncated" in warning for warning in warnings)
