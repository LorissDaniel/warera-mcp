"""Read-only procedure registry invariants.

These tests are the structural half of the "no write can ever be invoked"
guarantee: the registry is an explicit allowlist, every entry is a reviewed
read, and the client exposes no other transport verb.
"""

from __future__ import annotations

import pytest

from warera_mcp.auth.requirements import CredentialKind, resolve_requirement
from warera_mcp.warera import procedures as registry
from warera_mcp.warera.procedures import (
    ALLOWED_HTTP_METHOD,
    PROCEDURES,
    READ_ONLY_PROCEDURES,
    ProcedureParamError,
    UnknownProcedureError,
    get_procedure,
)

#: Substrings that would indicate a state-changing operation. This is a
#: heuristic backstop; the authoritative guarantee is the explicit snapshot in
#: ``test_registry_matches_the_reviewed_read_only_snapshot``.
MUTATION_MARKERS = (
    "create",
    "update",
    "delete",
    "remove",
    "placeorder",
    "cancel",
    "hire",
    "fire",
    "claim",
    "collect",
    "produce",
    "donate",
    "vote",
    "attack",
    "mutate",
    "execute",
    "withdraw",
    "transfer",
)

#: The reviewed upstream read surface for this release. Any change here is a
#: deliberate, reviewable act.
EXPECTED_READ_ONLY_PROCEDURES = frozenset(
    {
        "mu.getById",
        "mu.getManyPaginated",
        "ranking.getRanking",
        "upgrade.getUpgradeByTypeAndEntity",
        "itemTrading.getPrices",
        "tradingOrder.getTopOrders",
        "inventory.getById",
        "tradingOrder.getAllOrdersByOwner",
        "country.getAllCountries",
        "country.getCountryById",
        "region.getRegionsObject",
        "region.getById",
        "user.getUserLite",
        "user.getUserById",
        "search.searchUsers",
        "search.searchAnything",
        "user.getUsersByCountry",
        "company.getById",
        "company.getCompanies",
        "company.getProductionBonus",
        "company.getRecommendedRegionIdsByItemCode",
        "workOffer.getWageStats",
        "workOffer.getWorkOffersPaginated",
        "battle.getBattles",
        "battle.getById",
        "battle.getLiveBattleData",
        "battleRanking.getRanking",
        "event.getEventsPaginated",
        "article.getArticlesPaginated",
        "article.getArticleLiteById",
        "gameConfig.getGameConfig",
        "gameConfig.getDates",
    }
)


def test_registry_matches_the_reviewed_read_only_snapshot() -> None:
    assert READ_ONLY_PROCEDURES == EXPECTED_READ_ONLY_PROCEDURES


def test_transport_is_get_only() -> None:
    assert ALLOWED_HTTP_METHOD == "GET"


def test_registry_is_non_empty_and_deduplicated() -> None:
    assert len(READ_ONLY_PROCEDURES) == len(PROCEDURES) >= 20


def test_no_procedure_name_looks_like_a_mutation() -> None:
    offenders = [
        name
        for name in READ_ONLY_PROCEDURES
        if any(marker in name.lower() for marker in MUTATION_MARKERS)
    ]
    assert offenders == []


def test_every_procedure_declares_evidence_and_auth() -> None:
    for spec in PROCEDURES.values():
        assert spec.evidence, f"{spec.name} must record why it is trusted"
        assert spec.auth.mode in {"PUBLIC", "SINGLE", "ANY_OF"}
        assert spec.schema_version >= 1


def test_only_public_procedures_are_cacheable() -> None:
    for spec in PROCEDURES.values():
        if spec.cacheable:
            assert spec.is_public, f"{spec.name} is not public but claims to be cacheable"
            assert spec.ttl_seconds and spec.ttl_seconds > 0


def test_configuration_procedures_are_parameterless_public_get_cache_entries() -> None:
    config = get_procedure("gameConfig.getGameConfig")
    dates = get_procedure("gameConfig.getDates")
    assert config.is_public and config.accepted_params == frozenset() and config.ttl_seconds == 300
    assert dates.is_public and dates.accepted_params == frozenset() and dates.ttl_seconds == 30
    with pytest.raises(ProcedureParamError):
        config.validate_params({"skill": "production"})


def test_recommended_region_operation_is_any_of_key_or_jwt() -> None:
    spec = get_procedure("company.getRecommendedRegionIdsByItemCode")
    assert spec.auth.mode == "ANY_OF"
    assert set(spec.auth.required_kinds) == {CredentialKind.API_KEY, CredentialKind.JWT}
    # API key is preferred when both are available (least privilege).
    both = frozenset({CredentialKind.API_KEY, CredentialKind.JWT})
    assert resolve_requirement(spec.auth, both).selected is CredentialKind.API_KEY
    assert (
        resolve_requirement(spec.auth, frozenset({CredentialKind.JWT})).selected
        is CredentialKind.JWT
    )


def test_transaction_feed_is_not_registered() -> None:
    assert "transaction.getPaginatedTransactions" not in READ_ONLY_PROCEDURES


def test_mu_global_ranking_is_registered_as_public_read() -> None:
    spec = get_procedure("ranking.getRanking")
    assert spec.is_public and spec.cacheable
    assert spec.required_params == frozenset({"rankingType"})
    assert spec.domain.value == "military_units"


def test_unknown_procedure_lookup_fails_closed() -> None:
    with pytest.raises(UnknownProcedureError):
        get_procedure("executor.mutateEverything")


def test_param_validation_rejects_unknown_keys_and_missing_required() -> None:
    spec = get_procedure("tradingOrder.getTopOrders")
    spec.validate_params({"itemCode": "iron"})
    with pytest.raises(ProcedureParamError, match="unsupported parameters"):
        spec.validate_params({"itemCode": "iron", "userId": "u1"})
    with pytest.raises(ProcedureParamError, match="missing required parameters"):
        spec.validate_params({})


def test_param_validation_never_echoes_values() -> None:
    spec = get_procedure("user.getUserLite")
    secret = "SUPER-SECRET-VALUE"
    with pytest.raises(ProcedureParamError) as excinfo:
        spec.validate_params({"userId": secret, "unknown": secret})
    assert secret not in str(excinfo.value)


def test_registry_names_match_the_module_level_allowlist() -> None:
    assert frozenset(registry.PROCEDURES) == registry.READ_ONLY_PROCEDURES
