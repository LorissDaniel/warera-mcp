"""Public-only TTL/LRU cache with singleflight coalescing."""

from __future__ import annotations

import asyncio

import pytest

from warera_mcp.cache.public_ttl import CacheOutcome, PublicTtlCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


async def test_cache_hit_after_miss_and_expiry(fake_clock: FakeClock) -> None:
    calls = 0

    async def loader() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"calls": calls}

    cache = PublicTtlCache(max_entries=4, clock=fake_clock)
    key = PublicTtlCache.build_key("itemTrading.getPrices", {})

    value, outcome = await cache.get_or_load(key, 30, loader)
    assert outcome is CacheOutcome.MISS
    assert value == {"calls": 1}

    value, outcome = await cache.get_or_load(key, 30, loader)
    assert outcome is CacheOutcome.HIT
    assert value == {"calls": 1}

    fake_clock.now += 31
    _, outcome = await cache.get_or_load(key, 30, loader)
    assert outcome is CacheOutcome.MISS
    assert calls == 2


async def test_concurrent_identical_requests_are_coalesced() -> None:
    calls = 0

    async def loader() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return "value"

    cache = PublicTtlCache(max_entries=4)
    key = PublicTtlCache.build_key("country.getAllCountries", {})

    results = await asyncio.gather(*[cache.get_or_load(key, 60, loader) for _ in range(6)])
    assert calls == 1
    outcomes = [outcome for _, outcome in results]
    assert outcomes.count(CacheOutcome.MISS) == 1
    assert outcomes.count(CacheOutcome.JOIN) == 5
    assert cache.stats.joins == 5


async def test_failed_load_is_not_cached_and_propagates() -> None:
    attempts = 0

    async def loader() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("upstream exploded")

    cache = PublicTtlCache(max_entries=4)
    key = PublicTtlCache.build_key("region.getById", {"regionId": "r1"})

    with pytest.raises(RuntimeError):
        await cache.get_or_load(key, 60, loader)
    with pytest.raises(RuntimeError):
        await cache.get_or_load(key, 60, loader)
    assert attempts == 2
    assert len(cache) == 0
    assert not cache._inflight


async def test_disabled_cache_bypasses_storage() -> None:
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    cache = PublicTtlCache(max_entries=4, enabled=False)
    key = PublicTtlCache.build_key("itemTrading.getPrices", {})
    assert (await cache.get_or_load(key, 60, loader))[1] is CacheOutcome.BYPASS
    assert (await cache.get_or_load(key, 60, loader))[1] is CacheOutcome.BYPASS
    assert calls == 2
    assert len(cache) == 0


async def test_lru_eviction_respects_max_entries() -> None:
    async def loader() -> str:
        return "v"

    cache = PublicTtlCache(max_entries=2)
    for name in ("a", "b", "c"):
        await cache.get_or_load(PublicTtlCache.build_key("p", {"x": name}), 60, loader)
    assert len(cache) == 2
    assert cache.stats.evictions >= 1


def test_cache_keys_are_stable_and_param_order_independent() -> None:
    first = PublicTtlCache.build_key("p", {"a": 1, "b": 2})
    second = PublicTtlCache.build_key("p", {"b": 2, "a": 1})
    assert first == second
    assert first.startswith("p:v1:")


def test_cache_keys_include_schema_version() -> None:
    assert PublicTtlCache.build_key("p", {}, schema_version=1) != PublicTtlCache.build_key(
        "p", {}, schema_version=2
    )
