"""Credential handling and isolation at the MCP boundary.

Neither v1 tool requires credentials, so these tests exercise the seam directly:
the sanitized ``player_context`` parser, the error boundary's redaction, the
client's per-request auth selection, and proof that a submitted credential value
cannot surface in a tool result, a log record, an error payload or a cache key.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from tests.conftest import UpstreamStub, run_mcp
from warera_mcp import errors as app_errors
from warera_mcp.auth.credentials import CredentialError, PlayerRequestContext, parse_player_context
from warera_mcp.auth.requirements import CredentialKind
from warera_mcp.config import Settings
from warera_mcp.mcp_server.errors import redactor_from_arguments, render_error, tool_errors
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.errors import WareraServerError
from warera_mcp.warera.procedures import get_procedure

SECRET = "wae_SUPER_SECRET_VALUE_0123456789"


def test_redactor_is_built_from_raw_tool_arguments() -> None:
    redactor = redactor_from_arguments({"player_context": {"api_key": SECRET, "jwt": "a.b.c"}})
    assert redactor.has_secrets
    assert SECRET not in redactor.scrub(f"failed with {SECRET}")
    assert redactor_from_arguments({"player_context": "not-an-object"}).has_secrets is False
    assert redactor_from_arguments({}).has_secrets is False


def test_error_results_scrub_credentials() -> None:
    error = app_errors.authentication_rejected(f"rejected for {SECRET}", "op", credential="API_KEY")
    result = render_error(
        error, redactor=redactor_from_arguments({"player_context": {"api_key": SECRET}})
    )
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert SECRET not in result.content[0].text
    assert SECRET not in str(result.structuredContent)


def test_credential_input_is_validated_in_a_sanitized_helper() -> None:
    context = parse_player_context({"api_key": SECRET, "jwt": "header.payload.sig"})
    assert context.available_kinds() == frozenset({CredentialKind.API_KEY, CredentialKind.JWT})

    with pytest.raises(CredentialError) as excinfo:
        parse_player_context({"api_key": SECRET + "\n"})
    assert SECRET not in str(excinfo.value)


def build_secret_tool_server() -> FastMCP:
    """A test-only server whose tool accepts ``player_context`` exactly like a
    credential-aware future tool would."""
    mcp = FastMCP("credential-boundary")

    @mcp.tool(name="probe_secret", description="Test-only tool exercising player_context.")
    @tool_errors("probe_secret")
    async def probe_secret(player_context: Any = None) -> CallToolResult:
        parsed = parse_player_context(player_context)
        if not parsed.has_any_credential:
            raise app_errors.missing_authentication(
                "needs a credential",
                "probe_secret",
                required=("JWT",),
                available=(),
                missing=("JWT",),
                user_message="ask the user first",
            )
        # Deliberately echo the secret into an upstream error message to prove
        # the boundary redacts it.
        raise app_errors.authentication_rejected(
            f"upstream rejected token {parsed.secret_values()[0]}",
            "probe_secret",
            credential="JWT",
        )

    return mcp


def test_tool_boundary_never_returns_a_submitted_credential() -> None:
    server = build_secret_tool_server()

    async def scenario(session: Any) -> None:
        # A malformed payload (a bare string) must not be echoed by the framework.
        malformed = await session.call_tool("probe_secret", {"player_context": SECRET})
        assert malformed.isError is True
        assert SECRET not in str(malformed.content)
        assert SECRET not in str(malformed.structuredContent)

        oversized = await session.call_tool(
            "probe_secret", {"player_context": {"jwt": SECRET * 500}}
        )
        assert oversized.isError is True
        assert SECRET not in str(oversized.content)
        assert SECRET not in str(oversized.structuredContent)

        rejected = await session.call_tool("probe_secret", {"player_context": {"jwt": SECRET}})
        assert rejected.isError is True
        assert SECRET not in rejected.content[0].text
        assert SECRET not in str(rejected.structuredContent)

        missing = await session.call_tool("probe_secret", {})
        assert missing.isError is True
        assert missing.structuredContent["error"]["code"] == "MISSING_AUTHENTICATION"
        assert missing.structuredContent["error"]["user_message"]

    asyncio.run(_drive(server, scenario))


async def _drive(server: FastMCP, scenario: Any) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    async with connect(server._mcp_server) as session:
        await scenario(session)


async def test_client_sends_exactly_one_credential_per_request(
    settings: Settings, stub: UpstreamStub, cache: Any
) -> None:
    stub.route("company.getRecommendedRegionIdsByItemCode", [])
    client = WareraQueryClient(settings, cache=cache, transport=stub.transport())
    spec = get_procedure("company.getRecommendedRegionIdsByItemCode")
    try:
        both = PlayerRequestContext(api_key=SECRET, jwt="header.payload.signature")
        await client.query(spec, {"itemCode": "iron"}, credentials=both)
        headers = stub.headers_for(spec.name)[-1]
        assert headers["x-api-key"] == SECRET
        assert "cookie" not in headers

        jwt_only = PlayerRequestContext(jwt="header.payload.signature")
        await client.query(spec, {"itemCode": "iron"}, credentials=jwt_only)
        headers = stub.headers_for(spec.name)[-1]
        assert headers["cookie"] == "jwt=header.payload.signature"
        assert "x-api-key" not in headers
    finally:
        await client.aclose()


async def test_credentials_are_never_logged(
    settings: Settings, stub: UpstreamStub, caplog: pytest.LogCaptureFixture
) -> None:
    from warera_mcp.cache.public_ttl import PublicTtlCache
    from warera_mcp.observability.logging import configure_logging

    configure_logging("DEBUG", json_format=True)
    stub.route_status("company.getRecommendedRegionIdsByItemCode", 500)
    client = WareraQueryClient(
        settings, cache=PublicTtlCache(max_entries=4), transport=stub.transport()
    )
    try:
        with caplog.at_level(logging.DEBUG), pytest.raises(WareraServerError):
            await client.query(
                get_procedure("company.getRecommendedRegionIdsByItemCode"),
                {"itemCode": "iron"},
                credentials=PlayerRequestContext(api_key=SECRET),
            )
    finally:
        await client.aclose()

    captured = "\n".join(record.getMessage() for record in caplog.records)
    captured += "\n".join(str(record.__dict__) for record in caplog.records)
    assert SECRET not in captured


async def test_missing_credential_makes_no_upstream_request(
    settings: Settings, stub: UpstreamStub
) -> None:
    from warera_mcp.cache.public_ttl import PublicTtlCache

    client = WareraQueryClient(
        settings, cache=PublicTtlCache(max_entries=4), transport=stub.transport()
    )
    try:
        with pytest.raises(Exception) as excinfo:
            await client.query(
                get_procedure("company.getRecommendedRegionIdsByItemCode"), {"itemCode": "iron"}
            )
        assert "credential" in str(excinfo.value).lower()
    finally:
        await client.aclose()
    assert stub.requests == []


def test_real_missing_authentication_mapping_carries_the_cleartext_warning() -> None:
    from warera_mcp.application.common import map_upstream_error
    from warera_mcp.errors import ErrorCode
    from warera_mcp.warera.errors import WareraMissingCredential

    error = map_upstream_error(
        WareraMissingCredential((CredentialKind.JWT,)),
        operation="get_player_private_status",
        procedure="user.getMe",
        credentials=None,
    )
    assert error.code is ErrorCode.MISSING_AUTHENTICATION
    assert error.retryable is False
    assert error.missing == ("JWT",)
    assert error.required_auth == "JWT"
    assert "clear" in (error.user_message or "").lower()


def test_v1_tools_require_no_credentials(settings: Settings, stub: UpstreamStub) -> None:
    """The public tool surface must never ask for a credential."""
    stub.route("itemTrading.getPrices", {"iron": 1.0})

    async def scenario(session: Any) -> None:
        tools = await session.list_tools()
        assert all(
            "player_context" not in tool.inputSchema.get("properties", {}) for tool in tools.tools
        )
        result = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert result.isError is False

    run_mcp(settings, stub, scenario)


def test_secret_can_never_appear_in_a_cache_key() -> None:
    from warera_mcp.cache.public_ttl import PublicTtlCache

    key = PublicTtlCache.build_key("itemTrading.getPrices", {"itemCode": "iron"})
    assert SECRET not in key


def test_text_content_blocks_are_plain_text_not_secret() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text="MISSING_AUTHENTICATION: needs a credential")],
        structuredContent={"error": {"code": "MISSING_AUTHENTICATION"}},
        isError=True,
    )
    assert SECRET not in result.content[0].text
