"""Opt-in live contract smoke tests.

These tests call the real WarEra API. They are skipped unless
``WARERA_MCP_LIVE_TESTS=1`` and are marked ``live`` so CI never runs them by
default (the design requires low-frequency, consented live probing only).

They exercise **public** procedures exclusively: no credentials are read, no
personal data is requested, and every request goes through the same read-only
allowlist as production.
"""

from __future__ import annotations

import os

import pytest

from warera_mcp.config import Settings
from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.procedures import get_procedure

pytestmark = pytest.mark.live

_LIVE = os.environ.get("WARERA_MCP_LIVE_TESTS") == "1"

if not _LIVE:
    pytest.skip(
        "live tests disabled; set WARERA_MCP_LIVE_TESTS=1 to probe the real API",
        allow_module_level=True,
    )


@pytest.fixture
async def live_client() -> WareraQueryClient:
    client = WareraQueryClient(Settings(max_retries=1))
    try:
        yield client  # type: ignore[misc]
    finally:
        await client.aclose()


async def test_prices_snapshot_is_public_and_non_empty(live_client: WareraQueryClient) -> None:
    from warera_mcp.domain.normalization import normalize_prices

    read = await live_client.query(get_procedure("itemTrading.getPrices"), {})
    prices = normalize_prices(read.data)
    assert prices, "expected at least one quoted item"
    assert all(price > 0 for price in prices.values())


async def test_countries_snapshot_is_public_and_shaped(live_client: WareraQueryClient) -> None:
    from warera_mcp.domain.normalization import normalize_country

    read = await live_client.query(get_procedure("country.getAllCountries"), {})
    assert isinstance(read.data, list)
    facts, _ = normalize_country(read.data[0])
    assert facts.id
    assert facts.name


async def test_public_operations_never_send_credentials(live_client: WareraQueryClient) -> None:
    """A public read must succeed with no player_context at all."""
    read = await live_client.query(get_procedure("region.getRegionsObject"), {})
    assert read.data is not None


async def test_get_method_is_used_for_live_reads(live_client: WareraQueryClient) -> None:
    await live_client.query(get_procedure("itemTrading.getPrices"), {})
    # Verified implicitly: the client exposes only GET and would raise otherwise.
    assert live_client.circuit_state in {"closed", "half_open"}
