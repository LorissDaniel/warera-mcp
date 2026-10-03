"""Cancellation safety of the shared cache and the circuit breaker.

Both bugs were found in review: a cancelled cache leader used to cancel every
joiner, and a cancelled half-open probe used to leave the breaker closed to
traffic forever.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.conftest import UpstreamStub
from warera_mcp.cache.public_ttl import CacheOutcome, PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.errors import WareraCircuitOpen, WareraServerError
from warera_mcp.warera.procedures import get_procedure
from warera_mcp.warera.resilience import CircuitBreaker, CircuitState

SPEC = get_procedure("itemTrading.getPrices")


# ------------------------------------------------------------------------- cache
async def test_cancelling_the_first_waiter_does_not_fail_the_others() -> None:
    cache = PublicTtlCache(max_entries=4)
    started = asyncio.Event()

    async def loader() -> str:
        started.set()
        await asyncio.sleep(0.05)
        return "data"

    leader = asyncio.create_task(cache.get_or_load("k", 60, loader))
    await started.wait()
    followers = [asyncio.create_task(cache.get_or_load("k", 60, loader)) for _ in range(3)]
    await asyncio.sleep(0)

    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader

    results = await asyncio.gather(*followers)
    assert [value for value, _ in results] == ["data"] * 3
    assert all(outcome is CacheOutcome.JOIN for _, outcome in results)


async def test_load_result_is_cached_even_if_every_waiter_is_cancelled() -> None:
    cache = PublicTtlCache(max_entries=4)
    calls = 0

    async def loader() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "data"

    waiter = asyncio.create_task(cache.get_or_load("k", 60, loader))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await asyncio.sleep(0.05)  # the shared load finishes on its own
    value, outcome = await cache.get_or_load("k", 60, loader)
    assert (value, outcome, calls) == ("data", CacheOutcome.HIT, 1)


async def test_failed_load_reaches_every_waiter_and_is_not_cached() -> None:
    cache = PublicTtlCache(max_entries=4)

    async def loader() -> str:
        await asyncio.sleep(0.01)
        raise WareraServerError(503, "boom")

    waiters = [asyncio.create_task(cache.get_or_load("k", 60, loader)) for _ in range(3)]
    results = await asyncio.gather(*waiters, return_exceptions=True)

    assert all(isinstance(result, WareraServerError) for result in results)
    assert len(cache) == 0
    assert "k" not in cache._inflight


# --------------------------------------------------------------- breaker (unit)
def test_abandoned_probe_lets_the_next_request_probe_again() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=lambda: now[0])
    breaker.record_failure()
    now[0] = 10.0

    assert breaker.allow() is True  # probe reserved
    assert breaker.allow() is False  # only one probe at a time
    breaker.abandon_probe()
    assert breaker.allow() is True  # a new probe is admitted
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


# -------------------------------------------------------------- breaker (client)
async def test_cancelled_probe_does_not_wedge_the_client_breaker() -> None:
    settings = Settings(
        max_retries=0,
        circuit_failure_threshold=1,
        circuit_cooldown_seconds=0,
        outbound_rate_per_second=1000,
        outbound_rate_burst=1000,
    )
    mode = {"value": "fail"}
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if mode["value"] == "fail":
            return httpx.Response(503, json={})
        if mode["value"] == "hang":
            await release.wait()
        return httpx.Response(200, json={"result": {"data": {"iron": 1}}})

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(WareraServerError):
            await client.query(SPEC, {})  # opens the breaker

        mode["value"] = "hang"
        probe = asyncio.create_task(client.query(SPEC, {}))
        await asyncio.sleep(0.05)  # the probe is in flight
        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await probe

        mode["value"] = "ok"
        read = await client.query(SPEC, {})  # must not raise WareraCircuitOpen
        assert read.data == {"iron": 1}
        assert client.circuit_state == "closed"
    finally:
        await client.aclose()


async def test_open_breaker_still_fails_fast_during_cooldown() -> None:
    settings = Settings(max_retries=0, circuit_failure_threshold=1, circuit_cooldown_seconds=60)
    stub = UpstreamStub().route_status("itemTrading.getPrices", 503)
    client = WareraQueryClient(settings, transport=stub.transport())
    try:
        with pytest.raises(WareraServerError):
            await client.query(SPEC, {})
        with pytest.raises(WareraCircuitOpen):
            await client.query(SPEC, {})
    finally:
        await client.aclose()
    assert len(stub.requests) == 1
