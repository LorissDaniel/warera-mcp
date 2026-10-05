"""Company recommendations and minimum credential policy."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import UpstreamStub, error_code, run_mcp
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.config import Settings
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.procedures import PROCEDURES

BOTH = {"api_key": "wae_test_read_key", "jwt": "header.payload.signature"}
RECOMMENDATIONS = "company.getRecommendedRegionIdsByItemCode"


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
