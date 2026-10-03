"""Canonical error taxonomy: payload shape, retryability and redaction."""

from __future__ import annotations

import pytest

from warera_mcp import errors as app_errors
from warera_mcp.auth.redaction import SecretRedactor
from warera_mcp.errors import AppError, ErrorAction, ErrorCode


def test_codes_are_retryable_only_where_retrying_helps() -> None:
    assert app_errors.rate_limited("x", "op").retryable is True
    assert app_errors.upstream_timeout("x", "op").retryable is True
    assert app_errors.upstream_unavailable("x", "op").retryable is True
    assert app_errors.invalid_input("x", "op").retryable is False
    assert app_errors.not_found("x", "op").retryable is False
    assert app_errors.authentication_rejected("x", "op", credential="JWT").retryable is False
    assert app_errors.forbidden("x", "op", credential="API_KEY").retryable is False


def test_default_actions_are_actionable() -> None:
    assert app_errors.invalid_input("x", "op").action is ErrorAction.CORRECT_ARGUMENT
    assert (
        app_errors.missing_authentication(
            "x", "op", required=("JWT",), available=(), missing=("JWT",), user_message="warn"
        ).action
        is ErrorAction.ASK_USER_FOR_CREDENTIAL
    )
    assert app_errors.rate_limited("x", "op").action is ErrorAction.RETRY_LATER


def test_payload_matches_the_documented_shape() -> None:
    error = app_errors.not_found("no such player", "get_player", reason="username_no_match")
    body = error.to_payload()["error"]
    assert body["code"] == ErrorCode.NOT_FOUND.value
    assert body["message"] == "no such player"
    assert body["operation"] == "get_player"
    assert body["required_auth"] == ""
    assert body["available_auth"] == []
    assert body["missing"] == []
    assert body["retryable"] is False
    assert body["action"] == ErrorAction.CORRECT_ARGUMENT.value
    assert body["details"] == {"reason": "username_no_match"}


def test_single_requirement_renders_as_string_and_any_of_as_list() -> None:
    single = app_errors.authentication_rejected("x", "op", credential="JWT")
    assert single.to_payload()["error"]["required_auth"] == "JWT"

    any_of = app_errors.missing_authentication(
        "x",
        "op",
        required=("API_KEY", "JWT"),
        available=(),
        missing=("API_KEY", "JWT"),
        user_message="warn",
    )
    assert any_of.to_payload()["error"]["required_auth"] == ["API_KEY", "JWT"]


def test_missing_authentication_carries_the_cleartext_warning() -> None:
    error = app_errors.missing_authentication(
        "x",
        "op",
        required=("JWT",),
        available=(),
        missing=("JWT",),
        user_message="warn the user first",
    )
    assert error.to_payload()["error"]["user_message"] == "warn the user first"


def test_internal_error_carries_a_correlation_id_only() -> None:
    error = app_errors.internal_error("boom", "op", correlation_id="req-1")
    body = error.to_payload()["error"]
    assert body["correlation_id"] == "req-1"
    assert "boom" in body["message"]


def test_payload_is_scrubbed_of_credentials_by_the_redactor() -> None:
    secret = "wae_supersecretvalue"
    error = app_errors.authentication_rejected(f"rejected for {secret}", "op", credential="API_KEY")
    scrubbed = error.to_payload(redactor=SecretRedactor([secret]))
    assert secret not in str(scrubbed)
    assert "[redacted]" in str(scrubbed)


def test_app_error_is_an_exception_with_safe_str() -> None:
    error = app_errors.invalid_input("bad limit", "op")
    assert isinstance(error, Exception)
    assert str(error) == "INVALID_INPUT: bad limit"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: app_errors.invalid_input("m", "op"),
        lambda: app_errors.not_found("m", "op"),
        lambda: app_errors.rate_limited("m", "op", retry_after_seconds=2.0),
        lambda: app_errors.upstream_schema_changed("m", "op", procedure="p"),
        lambda: app_errors.internal_error("m", "op", correlation_id="c"),
    ],
)
def test_every_error_renders_a_serialisable_payload(factory: object) -> None:
    error = factory()  # type: ignore[operator]
    payload = error.to_payload()
    assert isinstance(payload, dict)
    assert set(payload["error"]) >= {"code", "message", "operation", "action"}


def test_exception_base_is_initialised_despite_slots_dataclass() -> None:
    error = AppError(code=ErrorCode.NOT_FOUND, message="m", operation="op")
    assert error.args == ("m",)
