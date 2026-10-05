"""Authenticated capabilities and minimum-credential transport policy."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.cache.public_ttl import PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.procedures import PROCEDURES, get_procedure

BOTH = {"api_key": "wae_test_read_key", "jwt": "header.payload.signature"}
INVENTORY = "inventory.getById"
ORDERS = "tradingOrder.getAllOrdersByOwner"
RECOMMENDATIONS = "company.getRecommendedRegionIdsByItemCode"


def seed_resources(stub: UpstreamStub) -> None:
    stub.route("user.getUserLite", {"_id": "u1", "username": "Loris"})
    stub.route("search.searchUsers", ["u1"])
    stub.route(
        INVENTORY,
        {
            "_id": "i1",
            "user": "u1",
            "money": 120,
            "items": {"basics": {"iron": 40, "fish": 2}},
            "market": {"basics": {"iron": 10}, "lockedMoney": 5},
            "managers": ["private"],
            "gems": 50,
            "__v": 1,
        },
    )
    stub.route(
        ORDERS,
        {
            "totalSellQuantities": {"iron": 10},
            "totalBuyMoneyInvested": 5,
            "totalSellMoneyExpected": 15,
        },
    )


def seed_locations(stub: UpstreamStub) -> None:
    stub.route(
        RECOMMENDATIONS,
        [
            {
                "regionId": "r1",
                "bonus": 55,
                "strategicBonus": 15,
                "ethicSpecializationBonus": 40,
                "depositBonus": 0,
                "ethicDepositBonus": 0,
                "taxPercent": 3,
            },
            {
                "regionId": "r2",
                "bonus": 30,
                "strategicBonus": 10,
                "ethicSpecializationBonus": 20,
                "depositBonus": 0,
                "ethicDepositBonus": 0,
                "taxPercent": 2,
            },
        ],
    )
    stub.route(
        "region.getRegionsObject",
        {
            "r1": {"name": "Region One", "country": "c1"},
            "r2": {"name": "Region Two", "country": "c1"},
        },
    )
    stub.route("country.getAllCountries", [{"_id": "c1", "name": "Country One"}])


@pytest.mark.parametrize("batching_enabled", [True, False])
def test_resources_use_only_jwt_and_keep_available_reserved_and_selling_separate(
    settings: Settings, stub: UpstreamStub, batching_enabled: bool
) -> None:
    seed_resources(stub)
    settings = settings.model_copy(update={"batching_enabled": batching_enabled})

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "username": "Loris",
                "item_codes": ["iron", "fish", "steel"],
                "player_context": BOTH,
            },
        )
        assert result.isError is False
        data = result.structuredContent
        assert data["money_available"] == 120
        assert data["money_reserved"] == 5
        assert data["items_available"] == {"iron": 40, "fish": 2, "steel": 0}
        assert data["items_reserved_for_market"] == {"iron": 10, "fish": 0, "steel": 0}
        assert data["sell_quantities"] == {"iron": 10, "fish": 0, "steel": 0}
        assert data["expected_sales_proceeds"] == 15
        assert data["partial"] is False
        assert "orders_observed_at" in data
        assert not any(key in data for key in ("gems", "managers", "__v"))
        assert not any(secret in json.dumps(data) for secret in BOTH.values())

    run_mcp(settings, stub, scenario)
    for procedure in (INVENTORY, ORDERS):
        assert json.loads(stub.calls(procedure)[0].url.params["input"]) == {"userId": "u1"}
        for headers in stub.headers_for(procedure):
            assert headers["cookie"] == "jwt=" + BOTH["jwt"]
            assert "x-api-key" not in headers
    for procedure in ("search.searchUsers", "user.getUserLite"):
        for headers in stub.headers_for(procedure):
            assert "cookie" not in headers and "x-api-key" not in headers


@pytest.mark.parametrize("context", [None, {"api_key": "wae_test_key"}])
def test_resource_tool_requires_jwt_without_attempting_network_reads(
    settings: Settings, stub: UpstreamStub, context: object
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "user_id": "u1",
                "player_context": context,
            },
        )
        assert error_code(result) == "MISSING_AUTHENTICATION"
        assert result.structuredContent["error"]["required_auth"] == "JWT"

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


def test_omitting_orders_avoids_order_read(settings: Settings, stub: UpstreamStub) -> None:
    seed_resources(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "user_id": "u1",
                "include_orders": False,
                "player_context": BOTH,
            },
        )
        assert result.isError is False
        assert result.structuredContent["orders_requested"] is False
        assert "sell_quantities" not in result.structuredContent
        assert result.structuredContent["partial"] is False

    run_mcp(settings, stub, scenario)
    assert not stub.calls(ORDERS)


def test_unavailable_orders_keep_inventory_and_do_not_invent_zero_sales(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_resources(stub)
    stub.route_envelope(
        ORDERS, {"error": {"data": {"code": "FORBIDDEN", "httpStatus": 403}}}, status=403
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "user_id": "u1",
                "player_context": BOTH,
            },
        )
        assert result.isError is False
        assert result.structuredContent["money_available"] == 120
        assert "sell_quantities" not in result.structuredContent
        assert result.structuredContent["partial"] is True
        assert any("FORBIDDEN" in warning for warning in result.structuredContent["warnings"])

    run_mcp(settings, stub, scenario)


def test_inventory_owner_mismatch_fails_closed(settings: Settings, stub: UpstreamStub) -> None:
    seed_resources(stub)
    stub.route(INVENTORY, {"_id": "i2", "user": "someone-else", "money": 99})

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "user_id": "u1",
                "player_context": BOTH,
            },
        )
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"
        assert "money_available" not in result.structuredContent

    run_mcp(settings, stub, scenario)


def test_rejected_inventory_jwt_is_not_blamed_on_the_supplied_api_key(
    settings: Settings, stub: UpstreamStub
) -> None:
    seed_resources(stub)
    stub.route_status(INVENTORY, 401)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "user_id": "u1",
                "include_orders": False,
                "player_context": BOTH,
            },
        )
        assert error_code(result) == "AUTHENTICATION_REJECTED"
        assert result.structuredContent["error"]["details"]["credential"] == "JWT"

    run_mcp(settings, stub, scenario)


def test_invalid_resource_identifier_does_not_request_jwt(
    settings: Settings, stub: UpstreamStub
) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player_resources", {"player_context": None})
        assert error_code(result) == "INVALID_INPUT"

    run_mcp(settings, stub, scenario)
    assert not stub.requests


def test_no_deposit_result_rejects_nonzero_deposit_terms(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(
        RECOMMENDATIONS,
        [
            {
                "regionId": "r1",
                "bonus": 70,
                "strategicBonus": 15,
                "ethicSpecializationBonus": 40,
                "depositBonus": 10,
                "ethicDepositBonus": 5,
                "taxPercent": 3,
            }
        ],
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_recommended_regions",
            {
                "item_code": "iron",
                "include_deposit": False,
                "player_context": BOTH,
            },
        )
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"

    run_mcp(settings, stub, scenario)


def test_missing_inventory_fields_are_unknown(settings: Settings, stub: UpstreamStub) -> None:
    seed_resources(stub)
    stub.route(
        INVENTORY, {"_id": "i1", "user": "u1", "money": True, "items": {"basics": {"iron": -1}}}
    )

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player_resources",
            {
                "user_id": "u1",
                "include_orders": False,
                "player_context": BOTH,
            },
        )
        assert result.isError is False
        for key in ("money_available", "items_available", "items_reserved_for_market"):
            assert key not in result.structuredContent
        assert result.structuredContent["partial"] is True

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize(
    "context,expected_header",
    [
        ({"api_key": BOTH["api_key"]}, "x-api-key"),
        (BOTH, "x-api-key"),
        ({"jwt": BOTH["jwt"]}, "cookie"),
    ],
)
def test_recommendations_use_the_least_required_credential_and_upstream_deposit_toggle(
    settings: Settings, stub: UpstreamStub, context: dict[str, str], expected_header: str
) -> None:
    seed_locations(stub)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_recommended_regions",
            {
                "item_code": "iron",
                "include_deposit": False,
                "limit": 1,
                "player_context": context,
            },
        )
        assert result.isError is False
        data = result.structuredContent
        assert data["include_deposit"] is False
        assert data["total_count"] == 2 and data["has_more"] is True
        first = data["regions"][0]
        assert first["production_bonus"]["total_fraction"] == 0.55
        assert first["production_bonus"]["components"]["deposit"] == 0
        assert first["tax_fraction"] == 0.03
        assert first["region_name"] == "Region One"
        assert first["country_name"] == "Country One"
        assert data["partial"] is False

    run_mcp(settings, stub, scenario)
    params = json.loads(stub.calls(RECOMMENDATIONS)[0].url.params["input"])
    assert params == {"itemCode": "iron", "includeDeposit": False}
    headers = stub.headers_for(RECOMMENDATIONS)[0]
    assert expected_header in headers
    assert ("cookie" if expected_header == "x-api-key" else "x-api-key") not in headers
    for procedure in ("region.getRegionsObject", "country.getAllCountries"):
        for headers in stub.headers_for(procedure):
            assert "cookie" not in headers and "x-api-key" not in headers


def test_recommendations_do_not_silently_downgrade_api_key_to_jwt(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route_status(RECOMMENDATIONS, 403)

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_recommended_regions",
            {
                "item_code": "iron",
                "player_context": BOTH,
            },
        )
        assert error_code(result) == "FORBIDDEN"

    run_mcp(settings, stub, scenario)
    assert len(stub.calls(RECOMMENDATIONS)) == 1
    assert "cookie" not in stub.headers_for(RECOMMENDATIONS)[0]


def test_recommendations_missing_auth_is_actionable(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_recommended_regions",
            {
                "item_code": "iron",
                "player_context": None,
            },
        )
        assert error_code(result) == "MISSING_AUTHENTICATION"
        assert result.structuredContent["error"]["required_auth"] == ["API_KEY", "JWT"]

    run_mcp(settings, stub, scenario)
    assert not stub.requests


def test_invalid_recommendation_bonus_is_not_fabricated(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(RECOMMENDATIONS, [{"regionId": "r1", "bonus": "55"}])

    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_recommended_regions",
            {
                "item_code": "iron",
                "player_context": BOTH,
            },
        )
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize("batching_enabled", [True, False])
async def test_every_public_procedure_ignores_both_supplied_credentials(
    settings: Settings, stub: UpstreamStub, batching_enabled: bool
) -> None:
    client = WareraQueryClient(
        settings.model_copy(update={"batching_enabled": batching_enabled}),
        transport=stub.transport(),
    )
    try:
        for spec in PROCEDURES.values():
            if not spec.is_public:
                continue
            stub.route(spec.name, {})
            await client.query(
                spec,
                dict.fromkeys(spec.required_params, "test"),
                credentials=PlayerRequestContext(**BOTH),
            )
    finally:
        await client.aclose()
    assert stub.requests
    for request in stub.requests:
        assert "x-api-key" not in request.headers and "cookie" not in request.headers


@pytest.mark.parametrize("procedure", [INVENTORY, ORDERS])
async def test_private_resource_reads_are_never_shared_cached(
    settings: Settings, stub: UpstreamStub, procedure: str
) -> None:
    stub.route(procedure, {"value": 1})
    cache = PublicTtlCache(max_entries=8)
    client = WareraQueryClient(settings, transport=stub.transport(), cache=cache)
    try:
        spec = get_procedure(procedure)
        await client.query(spec, {"userId": "u1"}, credentials=PlayerRequestContext(jwt="a.b.c"))
        stub.route(procedure, {"value": 2})
        second = await client.query(
            spec, {"userId": "u1"}, credentials=PlayerRequestContext(jwt="d.e.f")
        )
        assert second.data == {"value": 2}
        assert len(cache) == 0
        assert [h["cookie"] for h in stub.headers_for(procedure)] == ["jwt=a.b.c", "jwt=d.e.f"]
    finally:
        await client.aclose()
