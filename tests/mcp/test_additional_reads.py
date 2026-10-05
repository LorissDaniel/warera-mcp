"""Discovery, completeness and credential isolation for the additional read surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from tests.mcp.test_tool_calls import seed_public_routes
from warera_mcp.config import Settings

ENTRIES = json.loads(
    (Path(__file__).parents[1] / "contract/fixtures/additional_reads.json").read_text()
)["entries"]
FIXTURES = {entry["procedure"]: entry for entry in ENTRIES}


def seed(stub: UpstreamStub) -> None:
    seed_public_routes(stub)
    for entry in ENTRIES:
        stub.route(entry["procedure"], entry["data"])


def tool_calls() -> list[tuple[str, dict[str, Any]]]:
    def args(proc: str) -> dict[str, Any]:
        return FIXTURES[proc]["input"]

    return [
        ("get_player_equipment", {"user_id": "u1"}),
        ("get_round", {"round_id": args("round.getById")["roundId"]}),
        ("get_round_hits", {"round_id": "round1", "include_equipment": True}),
        (
            "get_battle_orders",
            {"battle_id": args("battleOrder.getByBattle")["battleId"], "side": "attacker"},
        ),
        (
            "get_battle_loot",
            {
                "battle_id": args("battleLootSummary.getByBattleAndUser")["battleId"],
                "user_id": args("battleLootSummary.getByBattleAndUser")["userId"],
            },
        ),
        ("search_mercenary_auctions", {"include_bids": True}),
        ("get_country_government", {"country_id": args("government.getByCountryId")["countryId"]}),
        ("get_work_offer", {"work_offer_id": args("workOffer.getById")["workOfferId"]}),
        ("get_work_offer", {"company_id": args("workOffer.getWorkOfferByCompanyId")["companyId"]}),
        ("get_workers", {"company_id": args("worker.getWorkers")["companyId"]}),
        ("search_transactions", {"military_unit_id": "mu1"}),
    ]


@pytest.mark.parametrize(("tool", "arguments"), tool_calls())
def test_new_tool_reads_verified_shape(
    settings: Settings, stub: UpstreamStub, tool: str, arguments: dict[str, Any]
) -> None:
    seed(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, arguments)
        assert result.isError is False, result
        assert result.structuredContent["observed_at"].endswith("Z")
        assert "__v" not in str(result.structuredContent)

    run_mcp(settings, stub, scenario)
    assert stub.methods == {"GET"}
    public = set(stub.procedures()) - {
        "worker.getWorkers",
        "worker.getTotalWorkersCount",
        "transaction.getPaginatedTransactions",
    }
    for procedure in public:
        assert all(
            "x-api-key" not in h and "authorization" not in h for h in stub.headers_for(procedure)
        )


@pytest.mark.parametrize(
    ("tool", "entity", "kind"),
    [
        ("get_company_upgrades", "company", "storage"),
        ("get_region_upgrades", "region", "bunker"),
    ],
)
def test_upgrade_scope_pending_dates_and_partial_failures(
    settings: Settings, stub: UpstreamStub, tool: str, entity: str, kind: str
) -> None:
    stub.route(
        "upgrade.getUpgradeByTypeAndEntity",
        {
            "_id": "up1",
            entity: "e1",
            "upgradeType": kind,
            "level": 0,
            "status": "disabled",
            "investedMoney": 0,
            "investedConcrete": 12,
            "lastUpgradeAt": "2026-10-05T10:00:00Z",
            "lastDowngradeAt": "2026-10-04T10:00:00Z",
        },
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, {entity + "_id": "e1", "upgrade_types": [kind]})
        assert not result.isError
        data = result.structuredContent
        assert data["partial"] is False
        upgrade = data["upgrades"][0]
        assert upgrade["level"] == 0 and upgrade["invested_money"] == 0
        assert upgrade["status"] == "disabled" and "last_downgrade_at" in upgrade
        all_types = await session.call_tool(tool, {entity + "_id": "e1"})
        assert all_types.structuredContent["partial"] is True
        assert len(all_types.structuredContent["upgrades"]) == 1
        assert len(all_types.structuredContent["unavailable_upgrade_types"]) == 2

    run_mcp(settings, stub, scenario)
    assert all(
        entity + "Id" in value and "muId" not in value
        for value in stub.inputs("upgrade.getUpgradeByTypeAndEntity")
    )


def test_absent_upgrade_does_not_invent_zero_level(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("upgrade.getUpgradeByTypeAndEntity", None)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_company_upgrades", {"company_id": "c1", "upgrade_types": ["storage"]}
        )
        assert result.structuredContent["upgrades"] == []
        assert result.structuredContent["absent_upgrade_types"] == ["storage"]
        assert result.structuredContent["partial"] is False

    run_mcp(settings, stub, scenario)


def test_equipment_empty_and_absent_slots_differ(settings: Settings, stub: UpstreamStub) -> None:
    seed(stub)
    stub.route(
        "inventory.fetchCurrentEquipment",
        {
            "weapon": None,
            "chest": {"_id": "eq1", "skills": {"armor": 0}, "state": 0, "maxState": 100},
        },
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player_equipment", {"username": "Kiro"})
        data = result.structuredContent
        assert data["empty_slots"] == ["weapon"]
        assert "pants" not in data["empty_slots"]
        assert data["equipment"]["chest"]["skills"]["armor"] == 0
        assert data["equipment"]["chest"]["state"] == 0

    run_mcp(settings, stub, scenario)


def test_hits_preserve_miss_damage_and_historical_equipment(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "round.getLastHits",
        {
            "attacker": [
                {
                    "_id": "h1",
                    "user": "u1",
                    "mu": "m1",
                    "damages": 177,
                    "isMissed": True,
                    "isCriticalHit": False,
                    "weapon": {"_id": "w1", "state": 30},
                    "equipments": [{"_id": f"e{i}", "skills": {"armor": 2}} for i in range(12)],
                }
            ],
            "defender": [],
        },
    )

    async def scenario(session: Any) -> None:
        compact = await session.call_tool("get_round_hits", {"round_id": "r1", "side": "attacker"})
        hit = compact.structuredContent["hits"]["attacker"][0]
        assert hit["is_missed"] is True and hit["damages"] == 177
        assert "weapon" not in hit and "equipment" not in hit
        expanded = await session.call_tool(
            "get_round_hits", {"round_id": "r1", "include_equipment": True}
        )
        hit = expanded.structuredContent["hits"]["attacker"][0]
        assert hit["weapon"]["state"] == 30 and len(hit["equipment"]) == 10
        assert hit["equipment_truncated"] is True and expanded.structuredContent["partial"] is True
        empty_page = await session.call_tool("get_round_hits", {"round_id": "r1", "offset": 1})
        assert empty_page.structuredContent["hits"] == {"attacker": [], "defender": []}
        assert empty_page.structuredContent["snapshot_counts"] == {"attacker": 1, "defender": 0}

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("round.getLastHits")) == 1


def test_order_redaction_does_not_mean_rank_zero(settings: Settings, stub: UpstreamStub) -> None:
    seed(stub)
    args = FIXTURES["battleOrder.getByBattle"]["input"]

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_battle_orders", {"battle_id": args["battleId"], "side": args["side"], "limit": 1}
        )
        data = result.structuredContent
        assert data["has_more"] is True and data["snapshot_count"] == 2
        order = data["orders"][0]
        assert order["text_and_rank_visibility"] == "restricted_in_anonymous_view"
        assert "rank" not in order and "text" not in order
        assert order["priority"] == "high"

    run_mcp(settings, stub, scenario)


def test_government_local_pages_do_not_drop_office_holders(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed(stub)
    cid = FIXTURES["government.getByCountryId"]["input"]["countryId"]

    async def scenario(session: Any) -> None:
        first = await session.call_tool("get_country_government", {"country_id": cid, "limit": 2})
        second = await session.call_tool(
            "get_country_government", {"country_id": cid, "limit": 2, "offset": 2}
        )
        assert first.structuredContent["roles"] == second.structuredContent["roles"]
        assert not (
            set(first.structuredContent["congress_member_ids"])
            & set(second.structuredContent["congress_member_ids"])
        )
        assert first.structuredContent["congress_member_count"] == 17

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("government.getByCountryId")) == 1


@pytest.mark.parametrize(
    ("tool", "args"),
    [("get_workers", {"user_id": "u1"}), ("search_transactions", {"user_id": "u1"})],
)
def test_api_key_is_required_before_network(
    settings: Settings, stub: UpstreamStub, tool: str, args: dict[str, Any]
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, {**args, "player_context": None})
        assert error_code(result) == "MISSING_AUTHENTICATION"
        assert "API_KEY" in str(result.structuredContent["error"]["required_auth"])

    run_mcp(settings, stub, scenario)
    assert not stub.requests


def test_transactions_filters_cursor_and_keys_are_isolated(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed(stub)

    async def scenario(session: Any) -> None:
        args = {
            "military_unit_id": "m1",
            "user_id": "u1",
            "country_id": "c1",
            "party_id": "p1",
            "item_code": "iron",
            "transaction_types": ["donation", "donation", "trading"],
            "limit": 2,
            "cursor": "opaque",
        }
        for key in ["wae_test_A", "wae_test_B"]:
            result = await session.call_tool(
                "search_transactions", {**args, "player_context": {"api_key": key}}
            )
            assert not result.isError and key not in str(result)
            row = result.structuredContent["transactions"][0]
            assert row["money"] == 5 and row["transaction_type"] == "donation"
            assert "seller_military_unit_id" in row and "buyer_user_id" in row

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("transaction.getPaginatedTransactions")) == 2
    assert [h["x-api-key"] for h in stub.headers_for("transaction.getPaginatedTransactions")] == [
        "wae_test_A",
        "wae_test_B",
    ]
    expected = {
        "muId": "m1",
        "userId": "u1",
        "countryId": "c1",
        "partyId": "p1",
        "itemCode": "iron",
        "transactionType": ["donation", "trading"],
        "limit": 2,
        "cursor": "opaque",
    }
    assert stub.inputs("transaction.getPaginatedTransactions") == [expected, expected]


def test_user_workers_empty_is_known_zero_and_count_failure_is_partial(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "worker.getWorkers",
        {
            "type": "user",
            "workersPerCompany": [{"company": {"_id": "c1", "name": "Factory"}, "workers": []}],
        },
    )
    stub.route("worker.getTotalWorkersCount", 0)

    async def scenario(session: Any) -> None:
        first = await session.call_tool("get_workers", {"user_id": "u1"})
        assert first.structuredContent["reported_total_workers_count"] == 0
        assert first.structuredContent["snapshot_count"] == 0
        assert first.structuredContent["companies"][0]["worker_count"] == 0
        stub.route_status("worker.getTotalWorkersCount", 503)
        second = await session.call_tool("get_workers", {"user_id": "u1"})
        assert second.structuredContent["partial"] is True
        assert "reported_total_workers_count" not in second.structuredContent

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("worker.getWorkers")) == 2


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_work_offer", {}),
        ("get_work_offer", {"work_offer_id": "w1", "company_id": "c1"}),
        ("get_workers", {}),
        ("get_workers", {"user_id": "u1", "company_id": "c1"}),
        ("get_round_hits", {"round_id": "r1", "side": "merged"}),
        ("get_battle_orders", {"battle_id": "b1", "side": "both"}),
        ("get_company_upgrades", {"company_id": "c1", "upgrade_types": ["bunker"]}),
        ("get_global_ranking", {"ranking_type": "unknown"}),
        ("search_mercenary_auctions", {"status": "unknown"}),
        ("search_transactions", {"transaction_types": ["unknown"]}),
    ],
)
def test_bad_new_inputs_never_reach_upstream(
    settings: Settings, stub: UpstreamStub, tool: str, args: dict[str, Any]
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(tool, args)
        assert result.isError is True
        if result.structuredContent is not None:
            assert error_code(result) == "INVALID_INPUT"

    run_mcp(settings, stub, scenario)
    assert not stub.requests


def test_malformed_identity_produces_schema_error_not_internal_error(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route("round.getById", {"_id": "wrong"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_round", {"round_id": "r1"})
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"

    run_mcp(settings, stub, scenario)


def test_rankings_keep_entity_links_and_tier_thresholds(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "ranking.getRanking",
        {
            "_id": "rank1",
            "type": "userDamages",
            "isGlobal": True,
            "tierValues": {"gold": 100},
            "items": [
                {
                    "user": "u1",
                    "mu": "m1",
                    "country": "c1",
                    "value": 10,
                    "rank": 1,
                    "tier": "bronze",
                },
                {"user": "u2", "value": 0, "rank": 2},
            ],
        },
    )

    async def scenario(session: Any) -> None:
        first = await session.call_tool(
            "get_global_ranking", {"ranking_type": "userDamages", "limit": 1}
        )
        assert (
            first.structuredContent["snapshot_count"] == 2
            and first.structuredContent["has_more"] is True
        )
        assert first.structuredContent["tier_values"] == {"gold": 100}
        assert first.structuredContent["entries"][0]["military_unit_id"] == "m1"
        second = await session.call_tool(
            "get_global_ranking", {"ranking_type": "userDamages", "limit": 1, "offset": 1}
        )
        assert second.structuredContent["entries"][0]["value"] == 0

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("ranking.getRanking")) == 1


def test_auction_bid_bounds_round_links_and_filters(settings: Settings, stub: UpstreamStub) -> None:
    stub.route(
        "mercenaryContractAuction.getPaginatedAuctions",
        {
            "items": [
                {
                    "_id": "a1",
                    "battle": "b1",
                    "round": "r1",
                    "roundNumber": 2,
                    "budget": 0,
                    "status": "won",
                    "bids": [
                        {"mu": "m1", "user": "u1", "perK": 0, "payout": 0},
                        {"mu": "m2", "perK": 1, "payout": 10},
                    ],
                }
            ],
            "nextCursor": "cursor2",
        },
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "search_mercenary_auctions",
            {
                "battle_id": "b1",
                "country_id": "c1",
                "status": "won",
                "cursor": "cursor1",
                "include_bids": True,
                "bid_limit": 1,
            },
        )
        data = result.structuredContent
        auction = data["auctions"][0]
        assert auction["round_id"] == "r1" and auction["round_number"] == 2
        assert auction["budget"] == 0 and auction["bid_count"] == 2
        assert auction["bids_truncated"] and data["partial"]
        assert len(auction["bids"]) == 1 and auction["bids"][0]["per_k"] == 0
        assert data["page"]["next_cursor"] == "cursor2"

    run_mcp(settings, stub, scenario)
    params = stub.inputs("mercenaryContractAuction.getPaginatedAuctions")[0]
    assert params == {
        "limit": 5,
        "battleId": "b1",
        "countryId": "c1",
        "status": "won",
        "cursor": "cursor1",
    }


def test_absent_loot_is_error_not_zero_rewards(settings: Settings, stub: UpstreamStub) -> None:
    stub.route_status("battleLootSummary.getByBattleAndUser", 404)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_battle_loot", {"battle_id": "b1", "user_id": "u1"})
        assert error_code(result) == "NOT_FOUND"
        assert "total_money_from_bounty" not in result.structuredContent

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize("members", [None, []])
def test_missing_congress_differs_from_empty(
    settings: Settings, stub: UpstreamStub, members: list[str] | None
) -> None:
    payload: dict[str, Any] = {"_id": "g1", "country": "c1"}
    if members is not None:
        payload["congressMembers"] = members
    stub.route("government.getByCountryId", payload)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_country_government", {"country_id": "c1"})
        data = result.structuredContent
        if members is None:
            assert "congress_member_ids" not in data and "congress_member_count" not in data
        else:
            assert data["congress_member_ids"] == [] and data["congress_member_count"] == 0

    run_mcp(settings, stub, scenario)


def test_country_players_page_preserves_ids_dates_and_cursor(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "user.getUsersByCountry",
        {"items": [{"_id": "u1", "createdAt": "2026-10-05T10:00:00Z"}], "nextCursor": "next"},
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_country_players", {"country_id": "c1", "limit": 1, "cursor": "previous"}
        )
        data = result.structuredContent
        assert data["players"][0] == {"user_id": "u1", "created_at": "2026-10-05T10:00:00Z"}
        assert data["page"]["next_cursor"] == "next"
        assert "total_count" not in data

    run_mcp(settings, stub, scenario)
    assert stub.inputs("user.getUsersByCountry") == [
        {"countryId": "c1", "limit": 1, "cursor": "previous"}
    ]
    assert "x-api-key" not in stub.headers_for("user.getUsersByCountry")[0]


def test_search_entity_groups_unknown_empty_and_local_pages(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        "search.searchAnything",
        {"userIds": ["u1", "u2"], "muIds": [], "allianceIds": ["a1"], "hasData": True},
    )

    async def scenario(session: Any) -> None:
        first = await session.call_tool("search_entities", {"search": "Example", "limit": 1})
        data = first.structuredContent
        assert data["entity_ids"] == {"user": ["u1"], "military_unit": [], "alliance": ["a1"]}
        assert "country" not in data["entity_ids"]
        assert data["has_more"]["user"] and data["snapshot_counts"]["user"] == 2
        second = await session.call_tool(
            "search_entities", {"search": "Example", "offset": 1, "limit": 1}
        )
        assert second.structuredContent["entity_ids"]["user"] == ["u2"]

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("search.searchAnything")) == 1
