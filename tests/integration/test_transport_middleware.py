"""Service-boundary middleware: health, client auth, rate limiting, headers, CORS.

The real ASGI app from ``build_http_app`` is driven through ``httpx.ASGITransport``
so the middleware chain is exercised as a remote MCP client would see it, without
depending on Starlette's (deprecated) test client. ASGITransport does not run
ASGI lifespan, so each scenario starts the MCP session manager explicitly, once,
inside a single event loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from tests.conftest import UpstreamStub, build_server
from warera_mcp.config import Settings
from warera_mcp.mcp_server.transport import build_http_app

CLIENT = ("203.0.113.7", 43210)

Send = Callable[..., Awaitable[httpx.Response]]


def run_http(
    settings: Settings,
    stub: UpstreamStub,
    scenario: Callable[[Send], Awaitable[None]],
) -> None:
    async def main() -> None:
        server = build_server(settings, stub)
        app: Any = build_http_app(server, settings)

        async def send(
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            json: Any = None,
            client: tuple[str, int] = CLIENT,
        ) -> httpx.Response:
            transport = httpx.ASGITransport(app=app, client=client)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
                return await http.request(method, path, headers=headers, json=json)

        async with server.session_manager.run():
            await scenario(send)

    asyncio.run(main())


def test_health_endpoint_is_local_and_needs_no_credentials(
    settings: Settings, stub: UpstreamStub
) -> None:
    async def scenario(send: Send) -> None:
        response = await send("GET", settings.health_path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    run_http(settings, stub, scenario)
    assert stub.requests == []


def test_security_headers_are_added_to_responses(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(send: Send) -> None:
        response = await send("GET", settings.health_path)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["cache-control"] == "no-store"

    run_http(settings, stub, scenario)


def test_client_auth_rejects_anonymous_callers(stub: UpstreamStub) -> None:
    settings = Settings(require_client_auth=True, client_auth_tokens=("client-token-value",))

    async def scenario(send: Send) -> None:
        anonymous = await send("POST", "/mcp", json={})
        assert anonymous.status_code == 401
        assert anonymous.headers["www-authenticate"] == "Bearer"

        wrong = await send("POST", "/mcp", json={}, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401

        allowed = await send(
            "POST", "/mcp", json={}, headers={"Authorization": "Bearer client-token-value"}
        )
        assert allowed.status_code != 401

    run_http(settings, stub, scenario)


def test_client_auth_is_disabled_by_default(settings: Settings, stub: UpstreamStub) -> None:
    async def scenario(send: Send) -> None:
        response = await send("POST", "/mcp", json={})
        assert response.status_code != 401

    run_http(settings, stub, scenario)


def test_health_bypasses_client_auth(stub: UpstreamStub) -> None:
    settings = Settings(require_client_auth=True, client_auth_tokens=("client-token-value",))

    async def scenario(send: Send) -> None:
        assert (await send("GET", settings.health_path)).status_code == 200

    run_http(settings, stub, scenario)


def test_rate_limit_returns_429_with_retry_after(stub: UpstreamStub) -> None:
    settings = Settings(client_rate_limit_per_minute=60, client_rate_limit_burst=2)

    async def scenario(send: Send) -> None:
        responses = [await send("POST", "/mcp", json={}) for _ in range(6)]
        rejected = [r for r in responses if r.status_code == 429]
        assert rejected
        assert rejected[0].headers["retry-after"].isdigit()

    run_http(settings, stub, scenario)


def test_rate_limit_is_scoped_per_client(stub: UpstreamStub) -> None:
    settings = Settings(client_rate_limit_per_minute=60, client_rate_limit_burst=1)

    async def scenario(send: Send) -> None:
        first = await send("POST", "/mcp", json={}, client=("198.51.100.1", 1000))
        second = await send("POST", "/mcp", json={}, client=("198.51.100.2", 1000))
        assert first.status_code != 429
        assert second.status_code != 429

    run_http(settings, stub, scenario)


def test_rate_limiter_never_touches_the_health_path(stub: UpstreamStub) -> None:
    settings = Settings(client_rate_limit_per_minute=60, client_rate_limit_burst=1)

    async def scenario(send: Send) -> None:
        for _ in range(5):
            assert (await send("GET", settings.health_path)).status_code == 200

    run_http(settings, stub, scenario)


def test_cors_is_opt_in(stub: UpstreamStub) -> None:
    preflight = {"Origin": "https://client.example", "Access-Control-Request-Method": "POST"}

    async def disabled(send: Send) -> None:
        response = await send("OPTIONS", "/mcp", headers=preflight)
        assert "access-control-allow-origin" not in response.headers

    run_http(Settings(), stub, disabled)

    async def enabled(send: Send) -> None:
        response = await send("OPTIONS", "/mcp", headers=preflight)
        assert response.headers["access-control-allow-origin"] == "https://client.example"

    run_http(
        Settings(enable_cors=True, cors_allow_origins=("https://client.example",)),
        stub,
        enabled,
    )
