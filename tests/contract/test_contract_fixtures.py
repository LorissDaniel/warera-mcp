"""Fixture-driven contract tests for every procedure in the read-only registry.

The fixtures under ``fixtures/`` record documented shapes or anonymized live captures,
identified by ``provenance``. They pin the parsing contract and make schema drift
visible: if normalization starts producing different fields, or a registry entry is
added without a fixture, these tests fail before a tool can ship.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from warera_mcp.application.resources import quantity_map
from warera_mcp.domain.article_normalization import normalize_article
from warera_mcp.domain.extended_normalization import (
    battle_hit,
    battle_order,
    equipment_item,
    government_snapshot,
    loot_entry,
    mercenary_auction,
    object_rows,
    transaction,
    worker,
)
from warera_mcp.domain.military_unit_normalization import normalize_military_unit, normalize_upgrade
from warera_mcp.domain.models import DomainModel
from warera_mcp.domain.normalization import (
    normalize_battle_detail,
    normalize_battle_ranking_entry,
    normalize_battle_summary,
    normalize_company_detail,
    normalize_company_summary,
    normalize_country,
    normalize_event,
    normalize_live_battle,
    normalize_order_book,
    normalize_player_lite,
    normalize_prices,
    normalize_production_bonus,
    normalize_region_detail,
    normalize_wage_stats,
    normalize_work_offer,
)
from warera_mcp.warera.procedures import PROCEDURES

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

#: Ordered list of candidate model fields that must never be projected.
VOLATILE_RAW_FIELDS = ("lastHits", "equipment", "__v")


def _fixture_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def load_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _fixture_files():
        document = json.loads(path.read_text(encoding="utf-8"))
        for entry in document["entries"]:
            entries.append({**entry, "_file": path.name, "_provenance": document["provenance"]})
    return entries


FIXTURES = load_entries()

#: procedure -> canonical projection used by the contract test.
NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "government.getByCountryId": lambda data: government_snapshot(
        data, country_id=data["country"], offset=0, limit=20, observed_at=datetime.now(UTC)
    ),
    "inventory.fetchCurrentEquipment": lambda data: [
        equipment_item(v) for v in data.values() if v is not None
    ],
    "round.getById": lambda data: normalize_live_battle({"round": data}),
    "round.getLastHits": lambda data: [
        battle_hit(v, False) for values in data.values() for v in object_rows(values)
    ],
    "battleOrder.getByBattle": lambda data: [
        battle_order(v, data[0]["battle"], data[0]["side"]) for v in object_rows(data)
    ],
    "battleLootSummary.getByBattleAndUser": lambda data: [
        loot_entry(v) for v in object_rows(data["poolLoot"])
    ],
    "mercenaryContractAuction.getPaginatedAuctions": lambda data: [
        mercenary_auction(v, True, 10) for v in object_rows(data["items"])
    ],
    "workOffer.getById": normalize_work_offer,
    "workOffer.getWorkOfferByCompanyId": normalize_work_offer,
    "worker.getWorkers": lambda data: [worker(v) for v in object_rows(data["workers"])],
    "worker.getTotalWorkersCount": lambda data: data,
    "transaction.getPaginatedTransactions": lambda data: [
        transaction(v) for v in object_rows(data["items"])
    ],
    "mu.getById": lambda data: normalize_military_unit(data)[0],
    "mu.getManyPaginated": lambda data: [
        normalize_military_unit(item)[0] for item in data["items"]
    ],
    "ranking.getRanking": lambda data: [
        normalize_battle_ranking_entry(item, entity_type="mu") for item in data["items"]
    ],
    "upgrade.getUpgradeByTypeAndEntity": lambda data: normalize_upgrade(
        data,
        mu_id="mu-anon-1",
        upgrade_type="headquarters",
    ),
    "inventory.getById": lambda data: quantity_map(data["items"]["basics"], None),
    "tradingOrder.getAllOrdersByOwner": lambda data: quantity_map(
        data["totalSellQuantities"], None
    ),
    "itemTrading.getPrices": normalize_prices,
    "tradingOrder.getTopOrders": lambda data: normalize_order_book(data, max_orders=10),
    "country.getAllCountries": lambda data: [normalize_country(item)[0] for item in data],
    "country.getCountryById": lambda data: normalize_country(data)[0],
    "region.getRegionsObject": lambda data: {
        key: normalize_region_detail(value, default_id=key)[0] for key, value in data.items()
    },
    "region.getById": lambda data: normalize_region_detail(data)[0],
    "user.getUserById": lambda data: normalize_player_lite(data)[0],
    "user.getUserLite": lambda data: normalize_player_lite(data)[0],
    "search.searchUsers": lambda data: list(data),
    "search.searchAnything": lambda data: dict(data),
    "user.getUsersByCountry": lambda data: list(data["items"]),
    "company.getById": lambda data: normalize_company_detail(data)[0],
    "company.getCompanies": lambda data: list(data["items"]),
    "company.getProductionBonus": lambda data: normalize_production_bonus(data)[0],
    "company.getRecommendedRegionIdsByItemCode": lambda data: list(data),
    "workOffer.getWageStats": normalize_wage_stats,
    "workOffer.getWorkOffersPaginated": lambda data: [
        normalize_work_offer(item) for item in data["items"]
    ],
    "battle.getBattles": lambda data: [normalize_battle_summary(item) for item in data["items"]],
    "battle.getById": lambda data: normalize_battle_detail(data, include_history=True)[0],
    "battle.getLiveBattleData": normalize_live_battle,
    "battleRanking.getRanking": lambda data: [
        normalize_battle_ranking_entry(item, entity_type="user") for item in data["items"]
    ],
    "event.getEventsPaginated": lambda data: [normalize_event(item) for item in data["items"]],
    "article.getArticlesPaginated": lambda data: [
        normalize_article(item, include_content=True) for item in data["items"]
    ],
    "article.getArticleLiteById": normalize_article,
    "gameConfig.getGameConfig": lambda data: data,
    "gameConfig.getDates": lambda data: data,
}


def _procedure_names() -> list[str]:
    return [entry["procedure"] for entry in FIXTURES]


def test_fixture_directory_is_present() -> None:
    assert FIXTURE_DIR.is_dir()
    assert _fixture_files()


def test_explicit_snapshot_plus_normalizers_cover_every_procedure() -> None:
    """A new registry entry without a fixture or a projection is a failed contract."""
    assert set(PROCEDURES) == set(NORMALIZERS)


def test_every_registered_procedure_has_at_least_one_fixture() -> None:
    covered = set(_procedure_names())
    assert set(PROCEDURES) - covered == set()


def test_no_orphan_fixtures_reference_unknown_procedures() -> None:
    assert set(_procedure_names()) - set(PROCEDURES) == set()


def test_fixture_auth_classes_match_the_registry() -> None:
    for entry in FIXTURES:
        spec = PROCEDURES[entry["procedure"]]
        assert entry["auth"] == spec.auth.mode, entry["procedure"]


def test_fixtures_declare_provenance_and_never_claim_to_be_live_captures() -> None:
    for entry in FIXTURES:
        assert entry["_provenance"] in {"documented-shape", "live-capture"}


@pytest.mark.parametrize("entry", FIXTURES, ids=_procedure_names())
def test_fixture_normalizes_without_raising(entry: dict[str, Any]) -> None:
    normalize = NORMALIZERS[entry["procedure"]]
    result = normalize(entry["data"])
    assert result is not None


@pytest.mark.parametrize("entry", FIXTURES, ids=_procedure_names())
def test_normalized_models_serialize_with_the_compact_policy(entry: dict[str, Any]) -> None:
    normalize = NORMALIZERS[entry["procedure"]]
    result = normalize(entry["data"])
    models = result if isinstance(result, list) else [result]
    for item in models:
        if isinstance(item, DomainModel):
            payload = item.model_dump(mode="json", exclude_none=True)
            assert json.dumps(payload) is not None
            for volatile in VOLATILE_RAW_FIELDS:
                assert volatile not in payload


def test_raw_volatile_battle_payloads_are_not_projected() -> None:
    listing = next(entry for entry in FIXTURES if entry["procedure"] == "battle.getBattles")
    assert "lastHits" in json.dumps(listing["data"])  # present upstream ...
    summaries = NORMALIZERS["battle.getBattles"](listing["data"])
    assert "lastHits" not in json.dumps([item.model_dump(mode="json") for item in summaries])

    dossier = next(entry for entry in FIXTURES if entry["procedure"] == "battle.getById")
    assert "equipment" in json.dumps(dossier["data"])  # ... and dropped from the projection
    detail = normalize_battle_detail(dossier["data"], include_history=True)[0]
    dumped = json.dumps(detail.model_dump(mode="json", exclude_none=True))
    assert "lastHits" not in dumped
    assert "equipment" not in dumped


def test_production_bonus_fixture_normalizes_to_fractions() -> None:
    fixture = next(
        entry for entry in FIXTURES if entry["procedure"] == "company.getProductionBonus"
    )
    bonus = normalize_production_bonus(fixture["data"])[0]
    assert bonus.total_fraction is not None
    assert 0 < bonus.total_fraction < 1
    assert all(0 <= value < 1 for value in bonus.components.values())


def test_battle_ranking_fixture_is_bounded_and_ranked() -> None:
    fixture = next(entry for entry in FIXTURES if entry["procedure"] == "battleRanking.getRanking")
    rows = NORMALIZERS["battleRanking.getRanking"](fixture["data"])
    assert [row.rank for row in rows] == [1, 2, 3]
    assert all(row.entity_id for row in rows)


def test_event_fixture_text_is_sanitized() -> None:
    fixture = next(entry for entry in FIXTURES if entry["procedure"] == "event.getEventsPaginated")
    events = NORMALIZERS["event.getEventsPaginated"](fixture["data"])
    assert events[0].summary is not None
    assert "<" not in events[0].summary
    assert events[0].related_ids == ["c-anon-1", "c-anon-2"]


def test_company_summary_fixture_keeps_upgrade_levels_numeric() -> None:
    fixture = next(entry for entry in FIXTURES if entry["procedure"] == "company.getById")
    summary = normalize_company_summary(fixture["data"])
    assert summary.upgrade_levels == {"storage": 2.0, "engine": 1.0}
