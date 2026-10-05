"""Military-unit intents, schema drift, output bounds and battle integration."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from warera_mcp.config import Settings

MU = {
    "_id": "mu1",
    "name": "Guard\u202e\nUnit",
    "user": "u1",
    "country": "c1",
    "region": "r1",
    "members": ["u1", "u2", "u3"],
    "roles": {"managers": ["u1"], "commanders": ["u2"]},
    "leveling": {"level": 2, "monthlyDamages": 0},
    "mercenaryReputation": 12.5,
    "activeUpgradeLevels": {"dormitories": 5},
    "rankings": {"muDamages": {"value": 100, "rank": 1, "tier": "master"}},
    "investedMoneyByUsers": {"u1": 1000},
    "__v": 123,
    "avatarUrl": "https://example.invalid/tracker",
}


def test_discovery_dossier_and_roster(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("mu.getManyPaginated", {"items": [MU], "nextCursor": "page2"})
    stub.route("mu.getById", MU)

    async def scenario(session: Any) -> None:
        response = await session.call_tool(
            "search_military_units",
            {
                "search": "Guard",
                "owner_id": "u1",
                "member_id": "u2",
                "limit": 1,
                "cursor": "page1",
                "player_context": {"api_key": "test-key"},
            },
        )
        assert response.isError is False
        result = response.structuredContent
        assert result["page"] == {"has_more": True, "next_cursor": "page2"}
        unit = result["military_units"][0]
        assert unit["name"] == "Guard Unit"
        assert unit["member_count"] == 3
        assert unit["commander_count"] == 1
        assert unit["monthly_damages"] == 0
        assert unit["active_upgrade_levels"] == {"dormitories": 5}
        assert unit["rankings"]["muDamages"]["value"] == 100
        assert "members" not in unit and "investedMoneyByUsers" not in unit
        assert "avatarUrl" not in unit and "__v" not in unit
        assert stub.inputs("mu.getManyPaginated") == [
            {
                "search": "Guard",
                "userId": "u1",
                "memberId": "u2",
                "limit": 1,
                "cursor": "page1",
            }
        ]
        detail = await session.call_tool("get_military_unit", {"military_unit_id": "mu1"})
        assert detail.isError is False
        assert detail.structuredContent["military_unit"] == unit
        roster = await session.call_tool(
            "get_military_unit_members",
            {
                "military_unit_id": "mu1",
                "offset": 1,
                "limit": 1,
            },
        )
        assert roster.isError is False
        assert roster.structuredContent["members"] == [
            {
                "user_id": "u2",
                "is_member": True,
                "is_owner": False,
                "is_manager": False,
                "is_commander": True,
            }
        ]
        assert roster.structuredContent["total_count"] == 3
        assert roster.structuredContent["has_more"] is True

    run_mcp(settings, stub, scenario)
    assert stub.methods == {"GET"}
    assert all(
        "authorization" not in r.headers and "x-api-key" not in r.headers for r in stub.requests
    )


def test_sparse_unit_keeps_unknown_counts_and_roles(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("mu.getById", {"_id": "mu1", "members": ["u1"]})

    async def scenario(session: Any) -> None:
        response = await session.call_tool("get_military_unit", {"military_unit_id": "mu1"})
        unit = response.structuredContent["military_unit"]
        assert "level" not in unit and "commander_count" not in unit
        roster = await session.call_tool("get_military_unit_members", {"military_unit_id": "mu1"})
        assert roster.structuredContent["members"] == [{"user_id": "u1", "is_member": True}]

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    "kind",
    [
        "muWeeklyDamages",
        "muDamages",
        "muTerrain",
        "muWealth",
        "muBounty",
        "muReputation",
    ],
)
def test_global_rankings_and_bounded_enrichment(
    settings: Settings,
    stub: UpstreamStub,
    kind: str,
) -> None:
    stub.route(
        "ranking.getRanking",
        {
            "items": [
                {"mu": f"mu{i}", "value": i * 100, "rank": i + 1, "tier": "gold"} for i in range(12)
            ]
        },
    )
    stub.route("mu.getById", MU)

    async def scenario(session: Any) -> None:
        response = await session.call_tool(
            "get_military_unit_ranking",
            {
                "ranking_type": kind,
                "offset": 1,
                "limit": 6,
            },
        )
        assert response.isError is False
        result = response.structuredContent
        assert len(result["entries"]) == 6
        assert result["entries"][0]["name"] == "Guard Unit"
        assert "name" not in result["entries"][-1]
        assert result["total_count"] == 12 and result["has_more"] is True
        assert stub.inputs("ranking.getRanking") == [{"rankingType": kind}]
        assert len(stub.inputs("mu.getById")) == min(5, settings.max_fanout)

    run_mcp(settings, stub, scenario)


def test_battle_mu_names_and_failed_enrichment(settings: Settings, stub: UpstreamStub) -> None:
    stub.route(
        "battleRanking.getRanking",
        {
            "items": [
                {"mu": "mu1", "value": 300, "rank": 1},
                {"mu": "unknown", "value": 200, "rank": 2},
            ]
        },
    )
    stub.route("mu.getById", MU)

    async def scenario(session: Any) -> None:
        response = await session.call_tool(
            "get_battle_ranking",
            {
                "battle_id": "b1",
                "entity_type": "mu",
            },
        )
        assert response.isError is False
        entries = response.structuredContent["entries"]
        assert entries[0]["name"] == "Guard Unit"
        assert entries[1]["entity_id"] == "unknown" and "name" not in entries[1]
        assert "some entity names" in " ".join(response.structuredContent["warnings"])

    run_mcp(settings, stub, scenario)


def test_upgrades_preserve_disabled_status_and_partial_data(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route("mu.getById", MU)
    stub.route(
        "upgrade.getUpgradeByTypeAndEntity",
        {
            "_id": "up1",
            "mu": "mu1",
            "upgradeType": "headquarters",
            "level": 4,
            "status": "disabled",
            "investedSteel": 1100,
            "willBeActiveAt": "2026-10-05T10:00:00Z",
        },
    )

    async def scenario(session: Any) -> None:
        response = await session.call_tool(
            "get_military_unit_upgrades", {"military_unit_id": "mu1"}
        )
        assert response.isError is False
        result = response.structuredContent
        assert result["partial"] is True  # dormitories returned a mismatched identity
        assert result["upgrades"][0]["status"] == "disabled"
        assert result["upgrades"][0]["invested_steel"] == 1100
        assert "invested_money" not in result["upgrades"][0]
        assert stub.inputs("upgrade.getUpgradeByTypeAndEntity") == [
            {"muId": "mu1", "upgradeType": "headquarters"},
            {"muId": "mu1", "upgradeType": "dormitories"},
        ]

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_military_units", {"limit": 21}),
        ("search_military_units", {"search": "x\n"}),
        ("search_military_units", {"cursor": "x" * 513}),
        ("get_military_unit", {"military_unit_id": ""}),
        ("get_military_unit_members", {"military_unit_id": "mu1", "offset": -1}),
        ("get_military_unit_ranking", {"ranking_type": "userWealth"}),
        ("get_military_unit_ranking", {"limit": 0}),
    ],
)
def test_invalid_inputs_never_reach_upstream(
    settings: Settings,
    stub: UpstreamStub,
    tool: str,
    arguments: dict[str, Any],
) -> None:
    async def scenario(session: Any) -> None:
        response = await session.call_tool(tool, arguments)
        assert response.isError is True

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


@pytest.mark.parametrize(
    ("tool", "procedure", "arguments", "payload", "code"),
    [
        ("search_military_units", "mu.getManyPaginated", {}, {}, "UPSTREAM_SCHEMA_CHANGED"),
        (
            "search_military_units",
            "mu.getManyPaginated",
            {},
            {"items": ["mu1"]},
            "UPSTREAM_SCHEMA_CHANGED",
        ),
        (
            "search_military_units",
            "mu.getManyPaginated",
            {"limit": 1},
            {"items": [MU, MU]},
            "UPSTREAM_SCHEMA_CHANGED",
        ),
        ("get_military_unit", "mu.getById", {"military_unit_id": "mu1"}, None, "NOT_FOUND"),
        (
            "get_military_unit",
            "mu.getById",
            {"military_unit_id": "mu2"},
            MU,
            "UPSTREAM_SCHEMA_CHANGED",
        ),
        (
            "get_military_unit_members",
            "mu.getById",
            {"military_unit_id": "mu1"},
            {"_id": "mu1"},
            "UPSTREAM_SCHEMA_CHANGED",
        ),
        (
            "get_military_unit",
            "mu.getById",
            {"military_unit_id": "mu1"},
            {"_id": "mu1", "members": [None]},
            "UPSTREAM_SCHEMA_CHANGED",
        ),
        (
            "get_military_unit_ranking",
            "ranking.getRanking",
            {},
            {"items": [{}]},
            "UPSTREAM_SCHEMA_CHANGED",
        ),
    ],
)
def test_schema_drift_and_missing_entities(
    settings: Settings,
    stub: UpstreamStub,
    tool: str,
    procedure: str,
    arguments: dict[str, Any],
    payload: Any,
    code: str,
) -> None:
    stub.route(procedure, payload)

    async def scenario(session: Any) -> None:
        assert error_code(await session.call_tool(tool, arguments)) == code

    run_mcp(settings, stub, scenario)


def test_unavailable_upgrades_keep_dossier_and_explicit_partial(
    settings: Settings,
    stub: UpstreamStub,
) -> None:
    stub.route("mu.getById", MU)
    stub.route_status("upgrade.getUpgradeByTypeAndEntity", 503)

    async def scenario(session: Any) -> None:
        response = await session.call_tool(
            "get_military_unit_upgrades", {"military_unit_id": "mu1"}
        )
        assert response.isError is False
        assert response.structuredContent["upgrades"] == []
        assert response.structuredContent["partial"] is True
        assert len(response.structuredContent["warnings"]) >= 3

    run_mcp(settings, stub, scenario)


def test_military_unit_output_budget(stub: UpstreamStub) -> None:
    stub.route("mu.getManyPaginated", {"items": [MU] * 20})

    async def scenario(session: Any) -> None:
        response = await session.call_tool("search_military_units", {"limit": 20})
        assert error_code(response) == "OUTPUT_TOO_LARGE"

    run_mcp(Settings(max_retries=0, max_output_bytes=4096), stub, scenario)


def test_ranking_and_roster_end_pages(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("mu.getById", MU)
    stub.route("ranking.getRanking", {"items": []})

    async def scenario(session: Any) -> None:
        ranking = await session.call_tool("get_military_unit_ranking", {})
        assert ranking.isError is False
        assert ranking.structuredContent["entries"] == []
        assert ranking.structuredContent["has_more"] is False
        assert stub.inputs("mu.getById") == []
        roster = await session.call_tool(
            "get_military_unit_members",
            {
                "military_unit_id": "mu1",
                "offset": 3,
            },
        )
        assert roster.structuredContent["members"] == []
        assert roster.structuredContent["has_more"] is False

    run_mcp(settings, stub, scenario)
