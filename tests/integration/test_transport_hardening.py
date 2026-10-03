"""Host validation, exposure warnings and rate-limiter identity/eviction behaviour."""

from __future__ import annotations

import io
import logging
from typing import Any

import pytest

from tests.conftest import UpstreamStub
from tests.integration.test_transport_middleware import Send, run_http
from warera_mcp.config import Settings
from warera_mcp.mcp_server.app import build_transport_security
from warera_mcp.mcp_server.transport import run_streamable_http
from warera_mcp.middleware.request_limits import ClientRateLimitMiddleware, _LocalBuckets
from warera_mcp.observability.logging import configure_logging

MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


# ------------------------------------------------------------- trusted hosts
def test_trusted_hosts_enable_dns_rebinding_protection() -> None:
    security = build_transport_security(Settings(trusted_hosts=("mcp.example.com",)))
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["mcp.example.com", "mcp.example.com:*"]


def test_explicit_ports_are_kept_verbatim() -> None:
    security = build_transport_security(Settings(trusted_hosts=("mcp.example.com:8443",)))
    assert security is not None
    assert security.allowed_hosts == ["mcp.example.com:8443"]


def test_cors_origins_become_the_allowed_origins() -> None:
    settings = Settings(
        trusted_hosts=("mcp.example.com",),
        enable_cors=True,
        cors_allow_origins=("https://client.example",),
    )
    security = build_transport_security(settings)
    assert security is not None
    assert security.allowed_origins == ["https://client.example"]


def test_without_trusted_hosts_the_sdk_default_applies() -> None:
    assert build_transport_security(Settings()) is None


def test_unlisted_host_header_is_rejected_and_listed_host_is_served(stub: UpstreamStub) -> None:
    settings = Settings(host="0.0.0.0", trusted_hosts=("mcp.example.com",), max_retries=0)  # noqa: S104

    async def scenario(send: Send) -> None:
        evil = await send(
            "POST", "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "host": "evil.test"}
        )
        assert evil.status_code == 421

        good = await send(
            "POST", "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "host": "mcp.example.com"}
        )
        assert good.status_code == 200

    run_http(settings, stub, scenario)


def test_health_endpoint_is_reachable_regardless_of_host_header(stub: UpstreamStub) -> None:
    settings = Settings(host="0.0.0.0", trusted_hosts=("mcp.example.com",))  # noqa: S104

    async def scenario(send: Send) -> None:
        response = await send("GET", settings.health_path, headers={"host": "10.0.0.5:8000"})
        assert response.status_code == 200

    run_http(settings, stub, scenario)


# ------------------------------------------------------------ exposure warnings
def test_public_bind_without_auth_or_hosts_warns_twice() -> None:
    notices = Settings(host="0.0.0.0").exposure_warnings()  # noqa: S104
    assert len(notices) == 2
    assert any("client authentication" in notice for notice in notices)
    assert any("trusted_hosts" in notice for notice in notices)


def test_loopback_bind_is_quiet() -> None:
    for host in ("127.0.0.1", "localhost", "::1"):
        assert Settings(host=host).exposure_warnings() == ()


def test_fully_configured_public_bind_is_quiet() -> None:
    settings = Settings(
        host="0.0.0.0",  # noqa: S104
        require_client_auth=True,
        client_auth_tokens=("token-value",),
        trusted_hosts=("mcp.example.com",),
    )
    assert settings.exposure_warnings() == ()


def test_starting_the_http_server_logs_the_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from warera_mcp.mcp_server.app import create_server

    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    settings = Settings(host="0.0.0.0")  # noqa: S104
    server = create_server(settings)  # configures logging, so capture afterwards
    stream = io.StringIO()
    configure_logging("INFO", json_format=True, stream=stream)

    run_streamable_http(server, settings)

    output = stream.getvalue()
    assert "without client authentication" in output
    assert '"level":"WARNING"' in output
    logging.getLogger().handlers.clear()


# ------------------------------------------------------- rate-limiter identity
def scope(peer: str = "10.0.0.1", forwarded: str | None = None) -> dict[str, Any]:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return {"type": "http", "path": "/mcp", "client": (peer, 1234), "headers": headers}


def test_forwarded_for_is_ignored_without_trusted_proxies() -> None:
    key = ClientRateLimitMiddleware._client_key(scope(forwarded="1.1.1.1"), 0)
    assert key == "10.0.0.1"


def test_one_trusted_proxy_uses_the_last_forwarded_entry() -> None:
    # The proxy appended 9.9.9.9; everything to its left is client-controlled.
    key = ClientRateLimitMiddleware._client_key(scope(forwarded="6.6.6.6, 7.7.7.7, 9.9.9.9"), 1)
    assert key == "9.9.9.9"


def test_two_trusted_proxies_use_the_second_from_the_right() -> None:
    key = ClientRateLimitMiddleware._client_key(scope(forwarded="6.6.6.6, 8.8.8.8, 9.9.9.9"), 2)
    assert key == "8.8.8.8"


def test_spoofed_left_entries_do_not_change_the_key() -> None:
    first = ClientRateLimitMiddleware._client_key(scope(forwarded="1.1.1.1, 9.9.9.9"), 1)
    second = ClientRateLimitMiddleware._client_key(scope(forwarded="2.2.2.2, 9.9.9.9"), 1)
    assert first == second == "9.9.9.9"


def test_missing_or_short_forwarded_header_falls_back_to_the_peer() -> None:
    assert ClientRateLimitMiddleware._client_key(scope(), 1) == "10.0.0.1"
    assert ClientRateLimitMiddleware._client_key(scope(forwarded="9.9.9.9"), 2) == "10.0.0.1"


def test_limits_are_applied_per_forwarded_client_behind_a_proxy(stub: UpstreamStub) -> None:
    settings = Settings(
        client_rate_limit_per_minute=60, client_rate_limit_burst=1, trusted_proxy_hops=1
    )

    async def scenario(send: Send) -> None:
        # Same proxy peer, different real clients: each gets its own bucket.
        a1 = await send("POST", "/mcp", json={}, headers={"x-forwarded-for": "1.1.1.1"})
        b1 = await send("POST", "/mcp", json={}, headers={"x-forwarded-for": "2.2.2.2"})
        a2 = await send("POST", "/mcp", json={}, headers={"x-forwarded-for": "1.1.1.1"})
        assert a1.status_code != 429
        assert b1.status_code != 429
        assert a2.status_code == 429

    run_http(settings, stub, scenario)


# ------------------------------------------------------- rate-limiter eviction
def make_buckets(max_clients: int, now: list[float]) -> _LocalBuckets:
    return _LocalBuckets(per_minute=60, burst=2, clock=lambda: now[0], max_clients=max_clients)


def test_a_flood_of_new_keys_cannot_reset_a_throttled_client() -> None:
    now = [0.0]
    buckets = make_buckets(3, now)
    assert buckets.consume("victim") == 0.0
    assert buckets.consume("victim") == 0.0
    assert buckets.consume("victim") > 0  # throttled

    for index in range(50):  # attacker rotates through fresh addresses
        buckets.consume(f"attacker-{index}")

    assert buckets.consume("victim") > 0, "throttled state must survive the flood"
    assert len(buckets) <= 4  # max_clients plus the shared overflow bucket


def test_new_keys_overflow_into_one_shared_bucket_when_the_table_is_full() -> None:
    now = [0.0]
    buckets = make_buckets(1, now)
    buckets.consume("occupant")
    waits = [buckets.consume(f"new-{index}") for index in range(5)]
    assert waits[0] == 0.0 and waits[1] == 0.0  # burst of 2 on the shared bucket
    assert all(wait > 0 for wait in waits[2:])


def test_idle_buckets_are_purged_so_normal_clients_are_never_pooled() -> None:
    now = [0.0]
    buckets = make_buckets(2, now)
    buckets.consume("a")
    buckets.consume("b")
    now[0] = 100.0  # both have fully refilled
    assert buckets.consume("c") == 0.0
    assert buckets.consume("c") == 0.0
    assert buckets.consume("c") > 0  # c has its own bucket (burst 2), not the overflow one
    assert len(buckets) <= 2


def test_a_rejected_client_stays_recent() -> None:
    now = [0.0]
    buckets = make_buckets(10, now)
    for _ in range(3):
        buckets.consume("noisy")
    buckets.consume("other")
    buckets.consume("noisy")
    assert list(buckets._buckets)[-1] == "noisy"
