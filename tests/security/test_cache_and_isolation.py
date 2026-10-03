"""Public-cache policy and cross-player isolation.

The cache must never hold authenticated data and credentials must never be
shared between concurrently executing requests.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.conftest import UpstreamStub
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.cache.public_ttl import CacheOutcome, PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.procedures import get_procedure

ANY_OF_PROCEDURE = "company.getRecommendedRegionIdsByItemCode"
PUBLIC_PROCEDURE = "itemTrading.getPrices"

KEY_A = "wae_player_a_key_000001"
KEY_B = "wae_player_b_key_000002"


def make_client(settings: Settings, stub: UpstreamStub, cache: PublicTtlCache) -> WareraQueryClient:
    return WareraQueryClient(settings, cache=cache, transport=stub.transport())


async def test_public_reads_are_cached(settings: Settings, stub: UpstreamStub) -> None:
    stub.route(PUBLIC_PROCEDURE, {"iron": 1.0})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    spec = get_procedure(PUBLIC_PROCEDURE)
    try:
        first = await client.query(spec, {})
        second = await client.query(spec, {})
    finally:
        await client.aclose()

    assert first.cache_outcome is CacheOutcome.MISS
    assert second.cache_outcome is CacheOutcome.HIT
    assert len(stub.calls(PUBLIC_PROCEDURE)) == 1


async def test_any_of_reads_are_never_cached(settings: Settings, stub: UpstreamStub) -> None:
    stub.route(ANY_OF_PROCEDURE, [])
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    spec = get_procedure(ANY_OF_PROCEDURE)
    credentials = PlayerRequestContext(api_key=KEY_A)
    try:
        first = await client.query(spec, {"itemCode": "iron"}, credentials=credentials)
        second = await client.query(spec, {"itemCode": "iron"}, credentials=credentials)
    finally:
        await client.aclose()

    assert first.cache_outcome is CacheOutcome.BYPASS
    assert second.cache_outcome is CacheOutcome.BYPASS
    assert len(stub.calls(ANY_OF_PROCEDURE)) == 2
    assert len(cache) == 0
    assert cache.stats.misses == 0


async def test_authenticated_response_does_not_populate_the_public_cache(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(ANY_OF_PROCEDURE, [{"regionId": "r1"}])
    stub.route(PUBLIC_PROCEDURE, {"iron": 1.0})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    try:
        await client.query(
            get_procedure(ANY_OF_PROCEDURE),
            {"itemCode": "iron"},
            credentials=PlayerRequestContext(jwt="a.b.c"),
        )
        assert len(cache) == 0
        await client.query(get_procedure(PUBLIC_PROCEDURE), {})
        assert len(cache) == 1
    finally:
        await client.aclose()


async def test_interleaved_player_credentials_do_not_leak(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(ANY_OF_PROCEDURE, [])
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    spec = get_procedure(ANY_OF_PROCEDURE)

    async def run(key: str) -> None:
        await client.query(
            spec, {"itemCode": "iron"}, credentials=PlayerRequestContext(api_key=key)
        )
        await asyncio.sleep(0)
        await client.query(
            spec, {"itemCode": "iron"}, credentials=PlayerRequestContext(api_key=key)
        )

    try:
        await asyncio.gather(run(KEY_A), run(KEY_B), run(KEY_A), run(KEY_B))
    finally:
        await client.aclose()

    headers = stub.headers_for(ANY_OF_PROCEDURE)
    assert len(headers) == 8
    seen = [header.get("x-api-key") for header in headers]
    assert set(seen) == {KEY_A, KEY_B}
    # No request ever carried two credentials, and no request carried the other
    # player's key.
    for header, key in zip(headers, seen, strict=True):
        assert header.get("cookie") is None
        assert key in {KEY_A, KEY_B}


async def test_cache_keys_are_param_scoped(settings: Settings, stub: UpstreamStub) -> None:
    stub.route("tradingOrder.getTopOrders", {"buyOrders": [], "sellOrders": []})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    spec = get_procedure("tradingOrder.getTopOrders")
    try:
        await client.query(spec, {"itemCode": "iron"})
        await client.query(spec, {"itemCode": "bread"})
        await client.query(spec, {"itemCode": "iron"})
    finally:
        await client.aclose()

    assert len(stub.calls("tradingOrder.getTopOrders")) == 2
    assert len(cache) == 2


async def test_cached_reads_keep_their_original_observation_time(
    settings: Settings, stub: UpstreamStub
) -> None:
    stub.route(PUBLIC_PROCEDURE, {"iron": 1.0})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    spec = get_procedure(PUBLIC_PROCEDURE)
    try:
        first = await client.query(spec, {})
        await asyncio.sleep(0.01)
        second = await client.query(spec, {})
    finally:
        await client.aclose()

    assert second.observed_at == first.observed_at


@pytest.mark.parametrize("key", [KEY_A, KEY_B])
async def test_credentials_never_appear_in_cache_contents(
    settings: Settings, stub: UpstreamStub, key: str
) -> None:
    stub.route(ANY_OF_PROCEDURE, [])
    stub.route(PUBLIC_PROCEDURE, {"iron": 1.0})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    try:
        await client.query(
            get_procedure(ANY_OF_PROCEDURE),
            {"itemCode": "iron"},
            credentials=PlayerRequestContext(api_key=key),
        )
        await client.query(get_procedure(PUBLIC_PROCEDURE), {})
        dumped: Any = cache._entries
        assert key not in str(dumped)
    finally:
        await client.aclose()
