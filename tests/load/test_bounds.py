"""Bounded-resource guarantees: fan-out, retries, coalescing and output size.

The design requires every tool to declare and honour an upstream call budget.
These tests assert those budgets against a recording stub rather than trusting
the implementation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC

import pytest

from tests.conftest import UpstreamStub
from warera_mcp.cache.public_ttl import CacheOutcome, PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.errors import WareraAuthError, WareraServerError
from warera_mcp.warera.procedures import get_procedure


def make_client(settings: Settings, stub: UpstreamStub, cache: PublicTtlCache) -> WareraQueryClient:
    return WareraQueryClient(settings, cache=cache, transport=stub.transport())


async def test_company_list_fanout_is_bounded_by_the_configured_cap() -> None:
    settings = Settings(max_retries=0, max_fanout=10, max_concurrent_requests=8)
    stub = UpstreamStub()
    stub.route("user.getUserLite", {"_id": "u1", "username": "Kiro"})
    stub.route("company.getCompanies", {"items": [f"co{index}" for index in range(50)]})
    stub.route("company.getById", {"name": "Works", "itemCode": "iron"})
    stub.route("company.getProductionBonus", {"total": 10})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    from warera_mcp.application import ServiceRuntime, build_services

    services = build_services(ServiceRuntime(client=client, settings=settings))
    try:
        result = await services.companies.get_player_companies(
            user_id="u1", username=None, limit=12, offset=0, include_bonus=True
        )
    finally:
        await client.aclose()

    assert result.total_count == 50
    assert len(result.companies) == settings.max_fanout
    assert result.has_more is True
    assert any("clamped" in warning for warning in result.warnings)
    # 1 listing + detail + bonus for the clamped page only.
    assert len(stub.calls("company.getById")) == settings.max_fanout
    assert len(stub.calls("company.getProductionBonus")) == settings.max_fanout
    assert len(stub.requests) <= 2 + 2 * settings.max_fanout


async def test_battle_ranking_truncates_a_large_upstream_payload() -> None:
    settings = Settings(max_retries=0)
    stub = UpstreamStub()
    stub.route(
        "battleRanking.getRanking",
        {
            "itemCount": 5000,
            "items": [
                {"user": f"u{index}", "rank": index + 1, "value": 1000 - index}
                for index in range(500)
            ],
        },
    )
    stub.route("user.getUserLite", {"username": "Kiro"})
    cache = PublicTtlCache(max_entries=8)
    client = make_client(settings, stub, cache)
    from warera_mcp.application import ServiceRuntime, build_services

    services = build_services(ServiceRuntime(client=client, settings=settings))
    try:
        result = await services.rankings.get_battle_ranking(
            battle_id="b1", entity_type="user", side="merged", metric="damage", limit=5
        )
    finally:
        await client.aclose()

    assert len(result.entries) == 5
    assert result.item_count == 5000
    assert result.truncated is True
    # Name enrichment is capped, never one lookup per upstream row.
    assert len(stub.calls("user.getUserLite")) <= 5


async def test_retries_are_bounded_by_max_retries() -> None:
    settings = Settings(max_retries=2, retry_base_backoff_seconds=0, retry_max_backoff_seconds=0)
    stub = UpstreamStub()
    stub.route_status("itemTrading.getPrices", 503)
    client = WareraQueryClient(
        settings,
        cache=PublicTtlCache(max_entries=4),
        transport=stub.transport(),
        sleeper=lambda _seconds: asyncio.sleep(0),
    )
    try:
        with pytest.raises(WareraServerError):
            await client.query(get_procedure("itemTrading.getPrices"), {})
    finally:
        await client.aclose()

    assert len(stub.calls("itemTrading.getPrices")) == settings.max_retries + 1


async def test_authorization_failures_are_never_retried() -> None:
    settings = Settings(max_retries=3)
    stub = UpstreamStub()
    stub.route_status("itemTrading.getPrices", 401)
    client = make_client(settings, stub, PublicTtlCache(max_entries=4))
    try:
        with pytest.raises(WareraAuthError):
            await client.query(get_procedure("itemTrading.getPrices"), {})
    finally:
        await client.aclose()

    assert len(stub.calls("itemTrading.getPrices")) == 1


async def test_concurrent_identical_public_reads_are_coalesced() -> None:
    settings = Settings(max_retries=0)
    stub = UpstreamStub()
    stub.route("itemTrading.getPrices", {"iron": 1.0})
    cache = PublicTtlCache(max_entries=4)
    client = make_client(settings, stub, cache)
    spec = get_procedure("itemTrading.getPrices")
    try:
        outcomes = await asyncio.gather(*[client.query(spec, {}) for _ in range(10)])
    finally:
        await client.aclose()

    assert len(stub.calls("itemTrading.getPrices")) == 1
    assert [read.cache_outcome for read in outcomes].count(CacheOutcome.MISS) == 1
    assert [read.cache_outcome for read in outcomes].count(CacheOutcome.JOIN) == 9


async def test_search_is_single_request_per_call() -> None:
    settings = Settings(max_retries=0)
    stub = UpstreamStub()
    stub.route("battle.getBattles", {"items": [], "nextCursor": None})
    client = make_client(settings, stub, PublicTtlCache(max_entries=4))
    from warera_mcp.application import ServiceRuntime, build_services

    services = build_services(ServiceRuntime(client=client, settings=settings))
    try:
        await services.battles.search_battles(country_id="c1", is_active=True, limit=5)
    finally:
        await client.aclose()

    assert len(stub.calls("battle.getBattles")) == 1


def test_output_budget_rejects_oversized_payloads() -> None:
    from datetime import datetime

    from warera_mcp.domain.models import CountryFacts, GetCountryOverviewResult, RegionSummary
    from warera_mcp.mcp_server.responses import enforce_output_budget, success_result

    model = GetCountryOverviewResult(
        observed_at=datetime(2024, 5, 1, tzinfo=UTC),
        country=CountryFacts(id="c1", name="Country"),
        regions=[RegionSummary(id=f"r{index}", name="Region" * 20) for index in range(200)],
    )
    with pytest.raises(Exception) as excinfo:
        success_result(model, summary="s", operation="get_country_overview", max_bytes=2048)
    assert "output budget" in str(excinfo.value)

    structured = model.to_structured()
    enforce_output_budget(structured, operation="op", max_bytes=10_000_000)


def test_registry_declares_a_ttl_for_every_cacheable_procedure() -> None:
    from warera_mcp.warera.procedures import PROCEDURES

    cacheable = [spec for spec in PROCEDURES.values() if spec.cacheable]
    assert cacheable
    for spec in cacheable:
        assert spec.ttl_seconds is not None
        assert 0 < spec.ttl_seconds <= 3600
        assert spec.is_public
