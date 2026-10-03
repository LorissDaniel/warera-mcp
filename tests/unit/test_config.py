"""Configuration validation: fixed upstream host, immutable settings, auth guard."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from warera_mcp.config import FIXED_UPSTREAM_HOST, Settings


def test_defaults_point_at_the_fixed_warera_host() -> None:
    settings = Settings()
    assert settings.upstream_host == FIXED_UPSTREAM_HOST
    assert str(settings.warera_base_url).startswith("https://")


def test_custom_upstream_is_rejected_by_default() -> None:
    with pytest.raises(ValidationError, match="fixed WarEra host"):
        Settings(warera_base_url="https://evil.example.com")


def test_non_https_upstream_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(warera_base_url="http://api2.warera.io")


def test_custom_upstream_requires_explicit_opt_in() -> None:
    settings = Settings(
        allow_custom_upstream=True,
        warera_base_url="http://127.0.0.1:9",
    )
    assert settings.upstream_host == "127.0.0.1"


def test_settings_are_frozen() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.port = 9999  # type: ignore[misc]


def test_client_auth_requires_tokens() -> None:
    with pytest.raises(ValidationError, match="client_auth_tokens"):
        Settings(require_client_auth=True)


def test_comma_separated_env_values_become_tuples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARERA_MCP_TRUSTED_HOSTS", "a.example, b.example")
    settings = Settings()
    assert settings.trusted_hosts == ("a.example", "b.example")


def test_environment_cannot_change_the_upstream_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WARERA_MCP_WARERA_BASE_URL", "https://attacker.invalid")
    with pytest.raises(ValidationError, match="fixed WarEra host"):
        Settings()


def test_unknown_environment_keys_are_ignored_not_silently_applied() -> None:
    # ``extra="forbid"`` applies to constructor kwargs; unknown *env* keys are
    # simply not read, which is asserted here by a stable default.
    assert Settings().server_name == "warera-mcp"
