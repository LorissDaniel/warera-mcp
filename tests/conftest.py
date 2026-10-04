"""Shared pytest fixtures: a programmable upstream stub and an MCP test harness.

No test in this suite talks to the real WarEra API unless it is explicitly marked
``live``. The stub records every request so tests can assert on method, procedure
and per-request auth headers — which is how the read-only and credential-isolation
guarantees are verified.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as connect

from warera_mcp.application import ServiceRuntime, Services, build_services
from warera_mcp.cache.public_ttl import PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.mcp_server.app import create_server
from warera_mcp.warera.client import WareraQueryClient

Route = tuple[int, Any, dict[str, str]]


class UpstreamStub:
    """Programmable, recording stand-in for the WarEra tRPC host."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[str, Route] = {}
        self._fallback: Route | None = None

    # -- programming ---------------------------------------------------------
    def route(
        self,
        procedure: str,
        data: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> UpstreamStub:
        self._routes[procedure] = (status, {"result": {"data": data}}, headers or {})
        return self

    def route_envelope(
        self,
        procedure: str,
        envelope: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> UpstreamStub:
        self._routes[procedure] = (status, envelope, headers or {})
        return self

    def route_status(
        self, procedure: str, status: int, *, headers: dict[str, str] | None = None
    ) -> UpstreamStub:
        self._routes[procedure] = (status, {"result": {"data": None}}, headers or {})
        return self

    def fallback(self, data: Any, *, status: int = 200) -> UpstreamStub:
        self._fallback = (status, {"result": {"data": data}}, {})
        return self

    # -- transport -----------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        procedure = request.url.path.rsplit("/", 1)[-1]
        if request.url.params.get("batch") == "1":
            payloads = []
            statuses = []
            for name in procedure.split(","):
                route = self._routes.get(name) or self._fallback
                status, payload, _ = route or (404, {}, {})
                if status >= 400 and "error" not in payload:
                    payload = {
                        "error": {"json": {"data": {"httpStatus": status, "code": str(status)}}}
                    }
                payloads.append(payload)
                statuses.append(status)
            status = statuses[0] if len(set(statuses)) == 1 else 207
            return httpx.Response(status, json=payloads)
        route = self._routes.get(procedure) or self._fallback
        if route is None:
            return httpx.Response(
                404,
                json={
                    "error": {
                        "json": {
                            "message": "stub has no route",
                            "data": {"code": "NOT_FOUND", "httpStatus": 404},
                        }
                    }
                },
            )
        status, payload, headers = route
        return httpx.Response(status, json=payload, headers=headers)

    # -- assertion helpers ---------------------------------------------------
    def _logical_requests(self) -> list[httpx.Request]:
        """Unpack batch items for assertions; requests still records physical HTTP."""
        logical: list[httpx.Request] = []
        for request in self.requests:
            if request.url.params.get("batch") != "1":
                logical.append(request)
                continue
            names = request.url.path.rsplit("/", 1)[-1].split(",")
            indexed = json.loads(request.url.params["input"])
            for index, name in enumerate(names):
                logical.append(
                    httpx.Request(
                        request.method,
                        request.url.copy_with(path="/trpc/" + name, query=None),
                        params={"input": json.dumps(indexed[str(index)])},
                        headers=request.headers,
                    )
                )
        return logical

    def procedures(self) -> list[str]:
        return [request.url.path.rsplit("/", 1)[-1] for request in self._logical_requests()]

    def calls(self, procedure: str) -> list[httpx.Request]:
        return [
            request
            for request in self._logical_requests()
            if request.url.path == "/trpc/" + procedure
        ]

    def inputs(self, procedure: str) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for request in self.calls(procedure):
            raw = request.url.params.get("input")
            parsed.append(json.loads(raw) if raw else {})
        return parsed

    def headers_for(self, procedure: str) -> list[dict[str, str]]:
        return [dict(request.headers) for request in self.calls(procedure)]

    @property
    def methods(self) -> set[str]:
        return {request.method for request in self.requests}

    def clear(self) -> None:
        self.requests.clear()


@pytest.fixture
def stub() -> UpstreamStub:
    return UpstreamStub()


@pytest.fixture
def settings() -> Settings:
    """Fast deterministic settings with production batching enabled."""
    return Settings(
        batch_window_seconds=0.005,
        max_retries=0,
        outbound_rate_per_second=1000,
        outbound_rate_burst=1000,
        max_concurrent_requests=16,
    )


@pytest.fixture
def cache(settings: Settings) -> PublicTtlCache:
    return PublicTtlCache(max_entries=settings.cache_max_entries)


@pytest_asyncio.fixture
async def query_client(
    settings: Settings, stub: UpstreamStub, cache: PublicTtlCache
) -> AsyncIterator[WareraQueryClient]:
    client = WareraQueryClient(settings, cache=cache, transport=stub.transport())
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def services(
    settings: Settings, stub: UpstreamStub, cache: PublicTtlCache
) -> AsyncIterator[Services]:
    client = WareraQueryClient(settings, cache=cache, transport=stub.transport())
    runtime = ServiceRuntime(client=client, settings=settings)
    try:
        yield build_services(runtime)
    finally:
        await client.aclose()


def build_server(settings: Settings, stub: UpstreamStub) -> FastMCP:
    return create_server(settings, transport=stub.transport())


def run_mcp(
    settings: Settings,
    stub: UpstreamStub,
    scenario: Callable[[Any], Awaitable[None]],
) -> None:
    """Run an MCP scenario against a stubbed upstream inside one event loop.

    The in-memory MCP session enters and exits an anyio task group, so it must be
    opened and closed inside the *same* task. A pytest async fixture cannot
    guarantee that, so scenarios are driven with ``asyncio.run`` here.
    """
    server = build_server(settings, stub)

    class CredentialedTestSession:
        """Inject a synthetic per-request key into production tool calls."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            payload = dict(arguments or {})
            if name not in {
                "probe_secret",
                "get_game_rules",
                "get_skill_progression",
                "get_item_details",
                "get_game_schedule",
            }:
                payload.setdefault("player_context", {"api_key": "wae_test_key"})
            return await self._inner.call_tool(name, payload, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    async def _main() -> None:
        async with connect(server._mcp_server) as session:
            await scenario(CredentialedTestSession(session))

    asyncio.run(_main())


def error_code(result: Any) -> str:
    """Extract the canonical error code from an MCP error result."""
    assert result.isError is True, result.content
    return str(result.structuredContent["error"]["code"])
