"""Capability requirements, credential handling and redaction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from warera_mcp.auth.credentials import (
    CredentialError,
    PlayerRequestContext,
    parse_player_context,
)
from warera_mcp.auth.redaction import SecretRedactor, contains_control_characters
from warera_mcp.auth.requirements import (
    AuthRequirement,
    CredentialKind,
    resolve_requirement,
)

BOTH = frozenset({CredentialKind.API_KEY, CredentialKind.JWT})
JWT_ONLY = frozenset({CredentialKind.JWT})
NONE: frozenset[CredentialKind] = frozenset()


def test_public_requirement_needs_nothing() -> None:
    requirement = AuthRequirement.public()
    resolution = resolve_requirement(requirement, NONE)
    assert resolution.satisfied
    assert resolution.selected is None
    assert requirement.describe() == "PUBLIC"


def test_single_requirement_is_exact() -> None:
    requirement = AuthRequirement.of(CredentialKind.JWT)
    assert resolve_requirement(requirement, JWT_ONLY).satisfied
    assert resolve_requirement(requirement, BOTH).selected is CredentialKind.JWT
    missing = resolve_requirement(requirement, NONE)
    assert not missing.satisfied
    assert missing.missing == (CredentialKind.JWT,)
    assert requirement.describe() == "JWT"


def test_any_of_prefers_the_api_key_and_accepts_either() -> None:
    requirement = AuthRequirement.any_of(CredentialKind.API_KEY, CredentialKind.JWT)
    assert resolve_requirement(requirement, BOTH).selected is CredentialKind.API_KEY
    assert resolve_requirement(requirement, JWT_ONLY).selected is CredentialKind.JWT
    assert not resolve_requirement(requirement, NONE).satisfied
    assert requirement.describe() == "API_KEY or JWT"


def test_any_of_requires_at_least_one_kind() -> None:
    with pytest.raises(ValueError):
        AuthRequirement.any_of()


def test_no_credential_hierarchy_is_inferred() -> None:
    """An API key must never satisfy a JWT-only requirement."""
    jwt_only = AuthRequirement.of(CredentialKind.JWT)
    assert not resolve_requirement(jwt_only, frozenset({CredentialKind.API_KEY})).satisfied


def test_context_reports_advertised_credentials_without_values() -> None:
    context = PlayerRequestContext(api_key="wae_abcdef123456", jwt="header.payload.signature")
    assert context.available_kinds() == BOTH
    assert context.has_any_credential
    assert "wae_abcdef123456" not in repr(context)
    assert "header.payload.signature" not in str(context)


def test_context_builds_exactly_one_credential_header() -> None:
    context = PlayerRequestContext(api_key="wae_abcdef123456", jwt="a.b.c")
    assert context.auth_headers(CredentialKind.API_KEY) == {"X-API-Key": "wae_abcdef123456"}
    assert context.auth_headers(CredentialKind.JWT) == {"Cookie": "jwt=a.b.c"}


def test_context_rejects_control_characters_and_oversized_values() -> None:
    with pytest.raises(ValidationError):
        PlayerRequestContext(api_key="bad\nvalue")
    with pytest.raises(ValidationError):
        PlayerRequestContext(jwt="x" * 9000)
    with pytest.raises(ValidationError):
        PlayerRequestContext(warera_user_id="x" * 100)


def test_parse_player_context_accepts_known_fields_only() -> None:
    context = parse_player_context({"warera_user_id": "u1", "warera_username": "Kiro"})
    assert context.warera_user_id == "u1"
    assert context.warera_username == "Kiro"
    assert not context.has_any_credential


def test_parse_player_context_rejects_unknown_fields() -> None:
    with pytest.raises(CredentialError, match="unsupported fields"):
        parse_player_context({"api_key": "wae_x", "admin": "true"})


def test_parse_player_context_rejects_non_mapping_payloads() -> None:
    with pytest.raises(CredentialError, match="must be an object"):
        parse_player_context("my-secret-jwt")


def test_credential_errors_never_echo_the_submitted_value() -> None:
    secret = "SUPER-SECRET-JWT-VALUE-1234567890"
    cases: list[object] = [
        {"jwt": secret, "unknown": 1},
        {"jwt": 12345},
        {"jwt": secret * 400},
        {"api_key": secret * 40},
        {"api_key": secret + "\n"},
        {"warera_username": secret + "\r\nX-Injected: 1"},
    ]
    for payload in cases:
        with pytest.raises(CredentialError) as excinfo:
            parse_player_context(payload)
        assert secret not in str(excinfo.value)


def test_secret_redactor_scrubs_nested_structures() -> None:
    redactor = SecretRedactor(["wae_abcdef123456", "a.b.c.signature"])
    payload = {
        "message": "cookie jwt=a.b.c.signature rejected",
        "nested": ["wae_abcdef123456", {"deep": "wae_abcdef123456"}],
    }
    scrubbed = redactor.scrub_object(payload)
    assert "a.b.c.signature" not in str(scrubbed)
    assert "wae_abcdef123456" not in str(scrubbed)
    assert "[redacted]" in str(scrubbed)


def test_secret_redactor_ignores_short_values() -> None:
    redactor = SecretRedactor(["abc"])
    assert redactor.scrub("abc def") == "abc def"
    assert not redactor.has_secrets


def test_control_character_detection() -> None:
    assert contains_control_characters("a\nb")
    assert contains_control_characters("a\x00b")
    assert not contains_control_characters("normal value")
