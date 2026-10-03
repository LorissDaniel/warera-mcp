"""Streaming size cap, admission control and the per-tool deadline."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from tests.conftest import UpstreamStub, error_code, run_mcp
from warera_mcp.config import Settings
from warera_mcp.mcp_server.app import create_server
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.errors import (
    WareraOverloaded,
    WareraResponseTooLarge,
    WareraSchemaError,
)
from warera_mcp.warera.procedures import get_procedure

PRICES = get_procedure("itemTrading.getPrices")


# ---------------------------------------------------------------- streaming cap
class CountingStream(httpx.AsyncByteStream):
    """An endless body that records how many chunks the client actually pulled."""

    def __init__(self, chunk: bytes, chunks: int) -> None:
        self.chunk = chunk
        self.total_chunks = chunks
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.total_chunks):
            self.pulled += 1
            yield self.chunk


async def test_body_read_stops_at_the_limit_instead_of_buffering_everything() -> None:
    settings = Settings(max_response_bytes=1024, max_retries=0)
    stream = CountingStream(b"x" * 256, chunks=100_000)  # ~25 MB if fully read

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)  # no Content-Length header

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(WareraResponseTooLarge):
            await client.query(PRICES, {})
    finally:
        await client.aclose()

    assert stream.pulled <= 6  # 1024 / 256 = 4 chunks, plus the one that crossed it


async def test_declared_content_length_is_rejected_before_any_body_is_read() -> None:
    settings = Settings(max_response_bytes=1024, max_retries=0)
    stream = CountingStream(b"x" * 256, chunks=10)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "999999"}, stream=stream)

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(WareraResponseTooLarge):
            await client.query(PRICES, {})
    finally:
        await client.aclose()

    assert stream.pulled == 0


async def test_error_statuses_are_classified_without_reading_the_body() -> None:
    settings = Settings(max_response_bytes=1024, max_retries=0)
    stream = CountingStream(b"x" * 256, chunks=100)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, stream=stream)

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Exception, match="upstream server error"):
            await client.query(PRICES, {})
    finally:
        await client.aclose()

    assert stream.pulled == 0


async def test_deeply_nested_json_is_a_schema_error_not_a_crash() -> None:
    settings = Settings(max_response_bytes=1_000_000, max_retries=0)
    nested = b"[" * 200_000 + b"]" * 200_000

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=nested)

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(WareraSchemaError):
            await client.query(PRICES, {})
    finally:
        await client.aclose()


async def test_invalid_utf8_is_a_schema_error() -> None:
    settings = Settings(max_retries=0)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xfe{")

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(WareraSchemaError):
            await client.query(PRICES, {})
    finally:
        await client.aclose()


# ------------------------------------------------------------ admission control
async def test_reads_beyond_the_pending_cap_are_shed_not_queued() -> None:
    settings = Settings(
        max_retries=0,
        max_concurrent_requests=1,
        max_pending_requests=3,
        outbound_rate_per_second=1000,
        outbound_rate_burst=1000,
    )
    gate = asyncio.Event()
    in_flight = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal in_flight
        in_flight += 1
        await gate.wait()
        return httpx.Response(200, json={"result": {"data": {"iron": 1}}})

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    spec = get_procedure("tradingOrder.getTopOrders")  # distinct keys avoid coalescing
    try:
        tasks = [
            asyncio.create_task(client.query(spec, {"itemCode": f"item{index}"}))
            for index in range(10)
        ]
        await asyncio.sleep(0.05)
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await client.aclose()

    shed = [result for result in results if isinstance(result, WareraOverloaded)]
    served = [result for result in results if not isinstance(result, BaseException)]
    assert shed, "excess reads must be rejected"
    assert len(served) + len(shed) == 10
    assert in_flight == len(served)


async def test_shed_reads_do_not_trip_the_circuit_breaker() -> None:
    settings = Settings(
        max_retries=0,
        max_concurrent_requests=1,
        max_pending_requests=1,
        circuit_failure_threshold=1,
        outbound_rate_per_second=1000,
        outbound_rate_burst=1000,
    )
    gate = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json={"result": {"data": {}}})

    client = WareraQueryClient(settings, transport=httpx.MockTransport(handler))
    spec = get_procedure("tradingOrder.getTopOrders")
    try:
        tasks = [
            asyncio.create_task(client.query(spec, {"itemCode": f"i{index}"})) for index in range(6)
        ]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert client.circuit_state == "closed"
    finally:
        await client.aclose()


def test_overload_maps_to_a_retryable_rate_limit_error(
    settings: Settings,
) -> None:
    from warera_mcp.application.common import map_upstream_error

    error = map_upstream_error(
        WareraOverloaded(), operation="get_market_price", procedure="x", credentials=None
    )
    assert error.code.value == "RATE_LIMITED"
    assert error.retryable is True


# ------------------------------------------------------------------ tool deadline
def test_tool_call_is_cut_off_at_the_deadline_with_a_timeout_error() -> None:
    settings = Settings(max_retries=0, tool_deadline_seconds=0.2)

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"result": {"data": {}}})

    async def main() -> None:
        server = create_server(settings, transport=httpx.MockTransport(slow))
        async with connect(server._mcp_server) as session:
            started = asyncio.get_running_loop().time()
            result = await session.call_tool("get_market_price", {"item_code": "iron"})
            elapsed = asyncio.get_running_loop().time() - started
            assert result.isError is True
            assert error_code(result) == "UPSTREAM_TIMEOUT"
            assert result.structuredContent["error"]["retryable"] is True
            assert "0.2s" in result.structuredContent["error"]["message"]
            assert elapsed < 2.0

    asyncio.run(main())


def test_fast_calls_are_unaffected_by_the_deadline(stub: UpstreamStub) -> None:
    settings = Settings(max_retries=0, tool_deadline_seconds=5)
    stub.route("itemTrading.getPrices", {"iron": 12.5})

    async def scenario(session: Any) -> None:
        result = await session.call_tool("get_market_price", {"item_code": "iron"})
        assert result.isError is False

    run_mcp(settings, stub, scenario)


def test_deadline_bounds_retry_backoff_and_fanout() -> None:
    """Retries with long Retry-After waits cannot outlive the tool budget."""
    settings = Settings(
        max_retries=3,
        tool_deadline_seconds=0.3,
        retry_max_retry_after_seconds=10,
        outbound_rate_per_second=1000,
        outbound_rate_burst=1000,
    )

    def always_429(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "10"}, json={})

    async def main() -> None:
        server = create_server(settings, transport=httpx.MockTransport(always_429))
        async with connect(server._mcp_server) as session:
            started = asyncio.get_running_loop().time()
            result = await session.call_tool("get_market_price", {"item_code": "iron"})
            assert asyncio.get_running_loop().time() - started < 2.0
            assert result.isError is True

    asyncio.run(main())


def test_settings_reject_nonsensical_budgets() -> None:
    for bad in ({"tool_deadline_seconds": 0}, {"max_pending_requests": 0}):
        with pytest.raises(ValueError):
            Settings(**bad)
