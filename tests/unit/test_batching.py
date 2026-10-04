"""Real GET batch encoding, isolation, partial retries, and bounded lifecycle."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from warera_mcp.application import ServiceRuntime, build_services
from warera_mcp.auth.credentials import parse_player_context
from warera_mcp.config import Settings
from warera_mcp.observability.metrics import InMemoryMetrics
from warera_mcp.warera.client import UpstreamRead, WareraQueryClient
from warera_mcp.warera.errors import (
    WareraAPIError,
    WareraOverloaded,
    WareraResponseTooLarge,
    WareraSchemaError,
    WareraServerError,
)
from warera_mcp.warera.procedures import get_procedure

PRICES = get_procedure("itemTrading.getPrices")
COUNTRIES = get_procedure("country.getAllCountries")
PROFILE = get_procedure("user.getUserLite")


def batch_settings(**overrides: object) -> Settings:
    return Settings(
        **{
            "batching_enabled": True,
            "batch_window_seconds": 0.005,
            "max_retries": 0,
            "outbound_rate_per_second": 1000,
            "outbound_rate_burst": 1000,
            **overrides,
        }
    )


def envelope(data: object) -> dict[str, object]:
    return {"result": {"data": data}}


def api_error(status: int) -> dict[str, object]:
    return {"error": {"json": {"data": {"httpStatus": status, "code": str(status)}}}}


class RecordingAPI:
    def __init__(self, route: Callable[[str, dict[str, object]], object] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.route = route or (lambda name, params: envelope({"name": name, "params": params}))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        names = request.url.path.removeprefix("/trpc/").split(",")
        params = json.loads(request.url.params["input"])
        if request.url.params.get("batch") == "1":
            payload = [self.route(name, params[str(i)]) for i, name in enumerate(names)]
            return httpx.Response(207, json=payload)
        return httpx.Response(200, json=self.route(names[0], params))

    def client(self, **overrides: object) -> WareraQueryClient:
        return WareraQueryClient(
            batch_settings(**overrides), transport=httpx.MockTransport(self.handler)
        )


async def test_parallel_reads_use_one_get_with_indexed_inputs_and_correct_results() -> None:
    api = RecordingAPI()
    metrics = InMemoryMetrics()
    async with WareraQueryClient(
        batch_settings(), transport=httpx.MockTransport(api.handler), metrics=metrics
    ) as client:
        reads = await asyncio.gather(client.query(PRICES), client.query(PROFILE, {"userId": "u1"}))
    assert len(api.requests) == 1
    request = api.requests[0]
    assert request.method == "GET"
    assert not request.content
    assert request.url.path == "/trpc/itemTrading.getPrices,user.getUserLite"
    assert json.loads(request.url.params["input"]) == {"0": {}, "1": {"userId": "u1"}}
    assert reads[1].data == {"name": PROFILE.name, "params": {"userId": "u1"}}
    assert metrics.counters[("warera_http_requests_total", ())] == 1
    assert metrics.counters[("warera_batches_total", ())] == 1


async def test_deduplication_is_canonical_and_does_not_cache_later_reads() -> None:
    api = RecordingAPI()
    spec = get_procedure("battle.getBattles")
    async with api.client() as client:
        reads = await asyncio.gather(
            client.query(spec, {"limit": 2, "isActive": True}),
            client.query(spec, {"isActive": True, "limit": 2}),
        )
        assert reads[0].data == reads[1].data
        assert len(api.requests) == 1
        assert "batch" not in api.requests[0].url.params
        await client.query(spec, {"limit": 2, "isActive": True})
    assert len(api.requests) == 2


async def test_public_api_key_and_jwt_calls_are_isolated() -> None:
    api = RecordingAPI()
    contexts = [
        None,
        parse_player_context({"api_key": "key-a"}),
        parse_player_context({"api_key": "key-b"}),
        parse_player_context({"jwt": "a.b.c"}),
    ]
    async with api.client() as client:
        await asyncio.gather(
            *(
                client.query(spec, credentials=context)
                for context in contexts
                for spec in (PRICES, COUNTRIES)
            )
        )
        assert not client._batcher._groups
    assert len(api.requests) == 4
    assert {(r.headers.get("x-api-key"), r.headers.get("cookie")) for r in api.requests} == {
        (None, None),
        ("key-a", None),
        ("key-b", None),
        (None, "jwt=a.b.c"),
    }
    assert all(r.url.params["batch"] == "1" for r in api.requests)


async def test_partial_error_does_not_discard_success() -> None:
    api = RecordingAPI(lambda name, _: api_error(404) if name == COUNTRIES.name else envelope(42))
    async with api.client() as client:
        outcomes = await asyncio.gather(
            client.query(PRICES), client.query(COUNTRIES), return_exceptions=True
        )
    assert isinstance(outcomes[0], UpstreamRead) and outcomes[0].data == 42
    assert isinstance(outcomes[1], WareraAPIError) and outcomes[1].http_status == 404
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [429, 503])
async def test_only_failed_retryable_items_are_retried(status: int) -> None:
    attempts: dict[str, int] = {}

    def route(name: str, _: dict[str, object]) -> object:
        attempts[name] = attempts.get(name, 0) + 1
        if name == COUNTRIES.name and attempts[name] == 1:
            return api_error(status)
        return envelope(name)

    api = RecordingAPI(route)
    async with api.client(max_retries=1, retry_base_backoff_seconds=0) as client:
        reads = await asyncio.gather(client.query(PRICES), client.query(COUNTRIES))
    assert attempts == {PRICES.name: 1, COUNTRIES.name: 2}
    assert [read.attempts for read in reads] == [1, 2]
    assert len(api.requests) == 2
    assert api.requests[1].url.path == "/trpc/" + COUNTRIES.name


async def test_full_batch_sends_before_collection_window() -> None:
    api = RecordingAPI()
    async with api.client(batch_max_size=2, batch_window_seconds=0.3) as client:
        await asyncio.wait_for(asyncio.gather(client.query(PRICES), client.query(COUNTRIES)), 0.15)
    assert len(api.requests) == 1


async def test_max_size_splits_batches() -> None:
    api = RecordingAPI()
    async with api.client(batch_max_size=2) as client:
        await asyncio.gather(*(client.query(PROFILE, {"userId": str(i)}) for i in range(5)))
    assert sorted(len(r.url.path.split(",")) for r in api.requests) == [1, 2, 2]


async def test_encoded_url_budget_splits_large_inputs() -> None:
    api = RecordingAPI()
    spec = get_procedure("battle.getBattles")
    # Cursor encoding expands non-ASCII bytes, so use actual encoded URL length.
    async with api.client(batch_max_url_bytes=1024) as client:
        await asyncio.gather(
            *(client.query(spec, {"cursor": "x" * 400 + str(i)}) for i in range(5))
        )
    assert len(api.requests) >= 3
    assert all(len(str(r.url).encode("ascii")) <= 1024 for r in api.requests)


async def test_cancellation_during_collection_removes_unused_reads() -> None:
    api = RecordingAPI()
    async with api.client(batch_window_seconds=0.05) as client:
        cancelled = asyncio.create_task(client.query(PRICES))
        other = asyncio.create_task(client.query(COUNTRIES))
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await other
    assert len(api.requests) == 1
    assert api.requests[0].url.path == "/trpc/" + COUNTRIES.name


async def test_cancelling_one_active_waiter_preserves_other_results() -> None:
    api = RecordingAPI()
    started, release = asyncio.Event(), asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return api.handler(request)

    async with WareraQueryClient(
        batch_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        first = asyncio.create_task(client.query(PRICES))
        second = asyncio.create_task(client.query(COUNTRIES))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second).data == {"name": COUNTRIES.name, "params": {}}


async def test_capacity_bounds_logical_reads_even_when_deduplicated() -> None:
    api = RecordingAPI()
    async with api.client(max_pending_requests=2, batch_window_seconds=0.02) as client:
        outcomes = await asyncio.gather(
            *(client.query(PRICES) for _ in range(6)), return_exceptions=True
        )
    assert sum(isinstance(x, WareraOverloaded) for x in outcomes) == 4
    assert sum(isinstance(x, UpstreamRead) for x in outcomes) == 2
    assert len(api.requests) == 1


async def test_shutdown_cancels_pending_reads_and_rejects_new_work() -> None:
    api = RecordingAPI()
    client = api.client(batch_window_seconds=0.3)
    task = asyncio.create_task(client.query(PRICES))
    await asyncio.sleep(0)
    await client.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(WareraOverloaded):
        await client.query(PRICES)
    assert not api.requests


@pytest.mark.parametrize(
    "payload", [[], [envelope(1)], [envelope(1), envelope(2), envelope(3)], {}]
)
async def test_invalid_batch_lengths_fail_every_item(payload: object) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    async with WareraQueryClient(batch_settings(), transport=transport) as client:
        outcomes = await asyncio.gather(
            client.query(PRICES), client.query(COUNTRIES), return_exceptions=True
        )
    assert all(isinstance(x, WareraSchemaError) for x in outcomes)


async def test_whole_batch_response_has_a_byte_limit() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2048))
    async with WareraQueryClient(
        batch_settings(max_response_bytes=1024), transport=transport
    ) as client:
        outcomes = await asyncio.gather(
            client.query(PRICES), client.query(COUNTRIES), return_exceptions=True
        )
    assert all(isinstance(x, WareraResponseTooLarge) for x in outcomes)


async def test_one_failed_http_batch_counts_once_against_circuit() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(503))
    async with WareraQueryClient(
        batch_settings(circuit_failure_threshold=2), transport=transport
    ) as client:
        outcomes = await asyncio.gather(
            client.query(PRICES), client.query(COUNTRIES), return_exceptions=True
        )
        assert all(isinstance(x, WareraServerError) for x in outcomes)
        assert client.circuit_state == "closed"
        with pytest.raises(WareraServerError):
            await client.query(PRICES)
        assert client.circuit_state == "open"


async def test_disabled_batching_uses_separate_requests() -> None:
    api = RecordingAPI()
    async with api.client(batching_enabled=False) as client:
        await asyncio.gather(client.query(PRICES), client.query(COUNTRIES))
    assert len(api.requests) == 2
    assert all("batch" not in r.url.params for r in api.requests)


async def test_company_service_batches_detail_and_bonus_without_cache() -> None:
    def route(name: str, params: dict[str, object]) -> object:
        if name == PROFILE.name:
            return envelope({"_id": "u1", "username": "Example"})
        if name == "company.getCompanies":
            return envelope({"items": ["co1", "co2", "co3", "co4", "co5"]})
        if name == "company.getById":
            return envelope({"_id": params["companyId"], "name": "Example", "itemCode": "iron"})
        return envelope({"total": 0.1})

    api = RecordingAPI(route)
    async with api.client(cache_enabled=False) as client:
        services = build_services(ServiceRuntime(client=client, settings=client._settings))
        result = await services.companies.get_player_companies(
            user_id="u1", username=None, limit=5, offset=0, include_bonus=True
        )
    assert len(result.companies) == 5
    assert len(api.requests) == 3  # profile, company IDs, ten enrichment reads
    assert len(api.requests[-1].url.path.split(",")) == 10


def test_default_window_is_300ms_and_invalid_batch_limits_are_rejected() -> None:
    assert Settings().batch_window_seconds == 0.3
    for overrides in (
        {"batch_window_seconds": -1},
        {"batch_max_size": 0},
        {"batch_max_url_bytes": 0},
    ):
        with pytest.raises(ValueError):
            Settings(**overrides)


async def test_cancelling_all_active_waiters_cancels_the_http_request() -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return httpx.Response(200)

    async with WareraQueryClient(
        batch_settings(), transport=httpx.MockTransport(handler)
    ) as client:
        tasks = [asyncio.create_task(client.query(spec)) for spec in (PRICES, COUNTRIES)]
        await started.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wait_for(cancelled.wait(), 0.5)
        assert client._semaphore._value == client._settings.max_concurrent_requests


async def test_shutdown_immediately_after_flush_resolves_every_waiter() -> None:
    api = RecordingAPI()
    client = api.client(batch_max_size=1)
    task = asyncio.create_task(client.query(PRICES))
    await asyncio.sleep(0)  # submit flushes, but dispatcher has not started yet
    await client.aclose()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.5)
    assert not api.requests


async def test_one_oversized_input_uses_single_query_format() -> None:
    api = RecordingAPI()
    spec = get_procedure("battle.getBattles")
    async with api.client(batch_max_url_bytes=1024) as client:
        await client.query(spec, {"cursor": "x" * 1500})
    assert "batch" not in api.requests[0].url.params


async def test_physical_http_concurrency_is_bounded() -> None:
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return httpx.Response(200, json=envelope(json.loads(request.url.params["input"])))

    async with WareraQueryClient(
        batch_settings(batch_max_size=1, max_concurrent_requests=2),
        transport=httpx.MockTransport(handler),
    ) as client:
        await asyncio.gather(*(client.query(PROFILE, {"userId": str(i)}) for i in range(10)))
    assert peak == 2
