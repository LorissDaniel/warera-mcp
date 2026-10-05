"""Player resolution honesty, advertised tool bounds, and error semantics."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tests.conftest import UpstreamStub, error_code, run_mcp
from warera_mcp.application.common import map_upstream_error
from warera_mcp.application.players import MAX_CANDIDATE_PROFILES
from warera_mcp.config import Settings
from warera_mcp.errors import ErrorAction, ErrorCode, output_too_large
from warera_mcp.mcp_server.app import create_server
from warera_mcp.mcp_server.responses import enforce_output_budget
from warera_mcp.mcp_server.tools.companies import MAX_COMPANIES_PER_CALL
from warera_mcp.warera.errors import WareraAuthError


def profile(index: int, name: str) -> dict[str, Any]:
    return {"_id": f"u{index}", "username": name}


# ------------------------------------------------------ M6: username resolution
def test_failed_candidate_lookups_are_not_reported_as_not_found(
    settings: Settings, stub: UpstreamStub
) -> None:
    """If a candidate could not be checked, 'no such player' would be a false claim."""
    stub.route("search.searchUsers", ["u1", "u2"])
    stub.route("user.getUserLite", None, status=503)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Kiro"})
        assert result.isError is True
        assert error_code(result) == "UPSTREAM_UNAVAILABLE"
        assert result.structuredContent["error"]["retryable"] is True

    run_mcp(settings, stub, scenario)


def test_unique_match_with_unchecked_candidates_carries_a_warning(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        procedure = request.url.path.rsplit("/", 1)[-1]
        if procedure == "search.searchUsers":
            return httpx.Response(200, json={"result": {"data": ["u1", "u2"]}})
        if request.url.params.get("batch") == "1":
            indexed = json.loads(request.url.params["input"])
            items = [
                {"error": {"json": {"data": {"code": "INTERNAL_SERVER_ERROR", "httpStatus": 503}}}}
                if params["userId"] == "u2"
                else {"result": {"data": profile(1, "Kiro")}}
                for params in indexed.values()
            ]
            return httpx.Response(207, json=items)
        if '"u2"' in str(request.url.params.get("input")):
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"result": {"data": profile(1, "Kiro")}})

    async def main() -> None:
        server = create_server(settings, transport=httpx.MockTransport(handler))
        async with connect(server._mcp_server) as session:
            result = await session.call_tool(
                "get_player", {"username": "kiro", "player_context": {"api_key": "wae_test_key"}}
            )
            assert result.isError is False
            warnings = result.structuredContent["warnings"]
            assert any("could not be checked" in warning for warning in warnings)
            assert result.structuredContent["player"]["id"] == "u1"

    import asyncio

    asyncio.run(main())


def test_saturated_search_results_advise_using_the_user_id(
    settings: Settings, stub: UpstreamStub
) -> None:
    ids = [f"u{index}" for index in range(MAX_CANDIDATE_PROFILES + 5)]
    stub.route("search.searchUsers", ids)
    stub.route("user.getUserLite", {"_id": "u0", "username": "KiroTwin"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Kiro"})
        assert error_code(result) == "NOT_FOUND"
        error = result.structuredContent["error"]
        assert error["details"]["reason"] == "username_not_in_top_candidates"
        assert "user_id" in error["message"]
        assert error["details"]["candidate_count"] == MAX_CANDIDATE_PROFILES

    run_mcp(settings, stub, scenario)


def test_small_result_sets_still_report_a_plain_no_exact_match(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route("search.searchUsers", ["u1"])
    stub.route("user.getUserLite", {"_id": "u1", "username": "SomeoneElse"})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player", {"username": "Kiro"})
        assert error_code(result) == "NOT_FOUND"
        assert result.structuredContent["error"]["details"]["reason"] == "username_no_exact_match"

    run_mcp(settings, stub, scenario)


def test_candidate_cap_is_ten_and_bounded_by_fanout(settings: Settings, stub: UpstreamStub) -> None:
    assert MAX_CANDIDATE_PROFILES == 10
    stub.route("search.searchUsers", [f"u{index}" for index in range(30)])
    stub.route("user.getUserLite", {"_id": "u0", "username": "Nope"})

    async def scenario(session: Any) -> None:
        await session.call_tool("get_player", {"username": "Kiro"})

    run_mcp(settings, stub, scenario)
    assert len(stub.calls("user.getUserLite")) == 10
    assert stub.inputs("search.searchUsers")[0]["limit"] == 10


def test_candidate_cap_respects_a_lower_fanout_setting(stub: UpstreamStub) -> None:
    low = Settings(max_retries=0, max_fanout=3)
    stub.route("search.searchUsers", [f"u{index}" for index in range(30)])
    stub.route("user.getUserLite", {"_id": "u0", "username": "Nope"})

    async def scenario(session: Any) -> None:
        await session.call_tool("get_player", {"username": "Kiro"})

    run_mcp(low, stub, scenario)
    assert len(stub.calls("user.getUserLite")) == 3


# ------------------------------------------------ M7 / L1: advertised tool bounds
def schemas(settings: Settings, stub: UpstreamStub) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    async def scenario(session: Any) -> None:
        for tool in (await session.list_tools()).tools:
            found[tool.name] = tool.inputSchema["properties"]

    run_mcp(settings, stub, scenario)
    return found


def test_companies_limit_is_reachable_with_default_settings(
    settings: Settings, stub: UpstreamStub
) -> None:
    props = schemas(settings, stub)["get_player_companies"]["limit"]
    assert props["maximum"] == MAX_COMPANIES_PER_CALL == Settings().max_fanout
    assert props["default"] == props["maximum"]


def test_default_company_call_is_never_clamped(stub: UpstreamStub) -> None:
    settings = Settings(max_retries=0)
    stub.route("user.getUserLite", {"_id": "u1", "username": "Kiro"})
    stub.route("company.getCompanies", {"items": [f"co{index}" for index in range(25)]})
    stub.route("company.getById", {"name": "Works", "itemCode": "iron"})
    stub.route("company.getProductionBonus", {"total": 10})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_player_companies", {"user_id": "u1"})
        assert result.isError is False
        data = result.structuredContent
        assert len(data["companies"]) == 10
        assert not any("clamped" in warning for warning in data["warnings"])
        assert data["has_more"] is True

        too_many = await session.call_tool("get_player_companies", {"user_id": "u1", "limit": 11})
        assert too_many.isError is True

    run_mcp(settings, stub, scenario)


def test_every_identifier_like_input_rejects_control_characters(
    settings: Settings, stub: UpstreamStub
) -> None:
    patterned = {
        ("get_player_companies", "user_id"),
        ("get_player_companies", "username"),
        ("search_battles", "country_id"),
        ("search_events", "country_id"),
        ("get_player", "user_id"),
        ("get_player", "username"),
    }
    all_schemas = schemas(settings, stub)
    for tool, name in patterned:
        prop = all_schemas[tool][name]
        string_schema = next(
            option for option in prop.get("anyOf", [prop]) if option.get("type") == "string"
        )
        assert "pattern" in string_schema, f"{tool}.{name} lacks a control-character pattern"
        assert "x00" in string_schema["pattern"]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("get_player_companies", {"user_id": "u1\nX"}),
        ("search_battles", {"country_id": "c1\r\n"}),
        ("search_events", {"country_id": "c1\x00"}),
        ("search_events", {"event_types": ["war\nX"]}),
    ],
)
def test_control_characters_are_refused_before_any_upstream_call(
    settings: Settings, stub: UpstreamStub, tool: str, arguments: dict[str, Any]
) -> None:
    async def scenario(session: Any) -> None:
        assert (await session.call_tool(tool, arguments)).isError is True

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


def test_event_type_filter_is_bounded(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(session: Any) -> None:
        too_many = await session.call_tool("search_events", {"event_types": ["war"] * 11})
        too_long = await session.call_tool("search_events", {"event_types": ["w" * 41]})
        empty_item = await session.call_tool("search_events", {"event_types": [""]})
        assert too_many.isError and too_long.isError and empty_item.isError

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


def test_event_type_filter_accepts_a_normal_request(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("event.getEventsPaginated", {"items": [], "nextCursor": None})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("search_events", {"event_types": ["war", "peace"]})
        assert result.isError is False

    run_mcp(settings, stub, scenario)


def test_player_field_list_is_bounded(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(session: Any) -> None:
        result = await session.call_tool(
            "get_player", {"user_id": "u1", "fields": ["profile"] * 11}
        )
        assert result.isError is True

    run_mcp(settings, stub, scenario)
    assert stub.requests == []


# ------------------------------------------------------------ L2: output too large
def test_output_budget_has_its_own_error_code() -> None:
    error = output_too_large("too big", "get_player_companies", bytes=10, limit_bytes=5)
    assert error.code is ErrorCode.OUTPUT_TOO_LARGE
    assert error.retryable is False
    assert error.action is ErrorAction.CORRECT_ARGUMENT
    assert error.details == {"bytes": 10, "limit_bytes": 5}


def test_enforce_output_budget_raises_the_new_code() -> None:
    with pytest.raises(Exception) as excinfo:
        enforce_output_budget({"x": "y" * 500}, operation="op", max_bytes=100)
    error = excinfo.value
    assert getattr(error, "code", None) is ErrorCode.OUTPUT_TOO_LARGE
    assert "narrower" in str(getattr(error, "message", ""))


def test_oversized_upstream_responses_still_map_to_schema_changed() -> None:
    from warera_mcp.warera.errors import WareraResponseTooLarge

    error = map_upstream_error(
        WareraResponseTooLarge(10), operation="op", procedure="p", credentials=None
    )
    assert error.code is ErrorCode.UPSTREAM_SCHEMA_CHANGED


# ----------------------------------------------- L3: upstream auth rejection
def test_refused_anonymous_call_is_not_blamed_on_a_credential() -> None:
    error = map_upstream_error(
        WareraAuthError(401),
        operation="get_market_price",
        procedure="itemTrading.getPrices",
        credentials=None,
    )
    assert error.code is ErrorCode.UPSTREAM_SCHEMA_CHANGED
    assert "UNKNOWN" not in error.message
    assert error.details["reason"] == "anonymous_access_refused"
    assert error.action is ErrorAction.CONTACT_OPERATOR


def test_rejected_request_credential_end_to_end(settings: Settings, stub: UpstreamStub) -> None:
    stub.route_status("company.getRecommendedRegionIdsByItemCode", 401)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_recommended_regions", {"item_code": "iron"})
        assert error_code(result) == "AUTHENTICATION_REJECTED"
        assert "UNKNOWN" not in result.content[0].text

    run_mcp(settings, stub, scenario)


@pytest.mark.parametrize("error_envelope", [False, True])
def test_public_refusal_ignores_supplied_credentials(
    settings: Settings, stub: UpstreamStub, error_envelope: bool
) -> None:
    if error_envelope:
        stub.route_envelope(
            "itemTrading.getPrices",
            {"error": {"data": {"code": "UNAUTHORIZED", "httpStatus": 401}}},
        )
    else:
        stub.route_status("itemTrading.getPrices", 401)

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert error_code(result) == "UPSTREAM_SCHEMA_CHANGED"
        assert result.structuredContent["error"]["action"] == "CONTACT_OPERATOR"

    run_mcp(settings, stub, scenario)
    assert "x-api-key" not in stub.headers_for("itemTrading.getPrices")[0]


def test_rejected_credential_still_names_the_credential_kind() -> None:
    from warera_mcp.auth.credentials import PlayerRequestContext

    error = map_upstream_error(
        WareraAuthError(401),
        operation="op",
        procedure="company.getRecommendedRegionIdsByItemCode",
        credentials=PlayerRequestContext(api_key="wae_synthetic_key_000001"),
    )
    assert error.code is ErrorCode.AUTHENTICATION_REJECTED
    assert "API_KEY" in error.message
    assert "wae_synthetic" not in error.message
