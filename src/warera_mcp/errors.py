"""Canonical, transport-agnostic error taxonomy.

This module owns the semantic error model from the design's error-handling
section. It deliberately has no MCP dependency so the application and domain
layers can raise actionable errors without depending on the server boundary.
:mod:`warera_mcp.mcp_server.errors` renders :class:`AppError` into an MCP
``CallToolResult``.

Invariants:

* Error payloads carry credential *kinds* only — never values, lengths, or
  fragments of submitted credentials.
* Messages are safe to show to a model/user and never contain raw upstream
  bodies or exception strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from warera_mcp.auth.redaction import SecretRedactor


class ErrorCode(StrEnum):
    """Actionable error codes referenced by the design's taxonomy table."""

    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
    MISSING_AUTHENTICATION = "MISSING_AUTHENTICATION"
    AUTHENTICATION_REJECTED = "AUTHENTICATION_REJECTED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_SCHEMA_CHANGED = "UPSTREAM_SCHEMA_CHANGED"
    PARTIAL_RESULT = "PARTIAL_RESULT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorAction(StrEnum):
    """Recommended next step for the caller/model."""

    NONE = "NONE"
    CORRECT_ARGUMENT = "CORRECT_ARGUMENT"
    ASK_USER_FOR_CREDENTIAL = "ASK_USER_FOR_CREDENTIAL"
    VERIFY_CREDENTIAL = "VERIFY_CREDENTIAL"
    RETRY_LATER = "RETRY_LATER"
    CONTACT_OPERATOR = "CONTACT_OPERATOR"


_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.UPSTREAM_TIMEOUT,
        ErrorCode.UPSTREAM_UNAVAILABLE,
        ErrorCode.PARTIAL_RESULT,
    }
)

_DEFAULT_ACTIONS: dict[ErrorCode, ErrorAction] = {
    ErrorCode.INVALID_INPUT: ErrorAction.CORRECT_ARGUMENT,
    ErrorCode.NOT_FOUND: ErrorAction.CORRECT_ARGUMENT,
    ErrorCode.AMBIGUOUS_ENTITY: ErrorAction.CORRECT_ARGUMENT,
    ErrorCode.MISSING_AUTHENTICATION: ErrorAction.ASK_USER_FOR_CREDENTIAL,
    ErrorCode.AUTHENTICATION_REJECTED: ErrorAction.VERIFY_CREDENTIAL,
    ErrorCode.FORBIDDEN: ErrorAction.NONE,
    ErrorCode.RATE_LIMITED: ErrorAction.RETRY_LATER,
    ErrorCode.UPSTREAM_TIMEOUT: ErrorAction.RETRY_LATER,
    ErrorCode.UPSTREAM_UNAVAILABLE: ErrorAction.RETRY_LATER,
    ErrorCode.UPSTREAM_SCHEMA_CHANGED: ErrorAction.CONTACT_OPERATOR,
    ErrorCode.PARTIAL_RESULT: ErrorAction.NONE,
    ErrorCode.INTERNAL_ERROR: ErrorAction.CONTACT_OPERATOR,
}


@dataclass(frozen=True, slots=True)
class AppError(Exception):
    """A sanitized, actionable application error."""

    code: ErrorCode
    message: str
    operation: str
    required_auth: str | tuple[str, ...] = ()
    available_auth: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    retryable: bool = False
    action: ErrorAction = ErrorAction.NONE
    user_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        # Exception has its own args handling; keep str(error) informative but
        # free of credential values.
        super().__init__(self.message)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code.value}: {self.message}"

    def to_payload(self, *, redactor: SecretRedactor | None = None) -> dict[str, Any]:
        """Return the canonical structured error object from the design."""
        required: str | list[str]
        if isinstance(self.required_auth, tuple):
            required = list(self.required_auth)
        else:
            required = self.required_auth

        payload: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "operation": self.operation,
            "required_auth": required,
            "available_auth": list(self.available_auth),
            "missing": list(self.missing),
            "alternatives": list(self.alternatives),
            "retryable": self.retryable,
            "action": self.action.value,
        }
        if self.user_message:
            payload["user_message"] = self.user_message
        if self.details:
            payload["details"] = self.details
        if self.correlation_id:
            payload["correlation_id"] = self.correlation_id

        if redactor is not None:
            payload = redactor.scrub_object(payload)  # type: ignore[assignment]
        return {"error": payload}

    @property
    def is_error_code_retryable(self) -> bool:
        return self.retryable


def _build(
    code: ErrorCode,
    message: str,
    operation: str,
    *,
    required_auth: str | tuple[str, ...] = (),
    available_auth: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    alternatives: tuple[str, ...] = (),
    retryable: bool | None = None,
    action: ErrorAction | None = None,
    user_message: str | None = None,
    details: dict[str, Any] | None = None,
) -> AppError:
    return AppError(
        code=code,
        message=message,
        operation=operation,
        required_auth=required_auth,
        available_auth=available_auth,
        missing=missing,
        alternatives=alternatives,
        retryable=code in _RETRYABLE if retryable is None else retryable,
        action=_DEFAULT_ACTIONS.get(code, ErrorAction.NONE) if action is None else action,
        user_message=user_message,
        details=details or {},
    )


def invalid_input(message: str, operation: str, **details: Any) -> AppError:
    return _build(ErrorCode.INVALID_INPUT, message, operation, details=details or None)


def not_found(message: str, operation: str, **details: Any) -> AppError:
    return _build(ErrorCode.NOT_FOUND, message, operation, details=details or None)


def ambiguous_entity(
    message: str, operation: str, *, candidates: tuple[str, ...] = (), **details: Any
) -> AppError:
    merged: dict[str, Any] = dict(details)
    if candidates:
        merged["candidates"] = list(candidates)
    return _build(ErrorCode.AMBIGUOUS_ENTITY, message, operation, details=merged or None)


def missing_authentication(
    message: str,
    operation: str,
    *,
    required: tuple[str, ...],
    available: tuple[str, ...],
    missing: tuple[str, ...],
    user_message: str,
) -> AppError:
    return _build(
        ErrorCode.MISSING_AUTHENTICATION,
        message,
        operation,
        required_auth=required if len(required) > 1 else (required[0] if required else ""),
        available_auth=available,
        missing=missing,
        user_message=user_message,
    )


def authentication_rejected(message: str, operation: str, *, credential: str) -> AppError:
    return _build(
        ErrorCode.AUTHENTICATION_REJECTED,
        message,
        operation,
        required_auth=credential,
        details={"credential": credential},
    )


def forbidden(message: str, operation: str, *, credential: str) -> AppError:
    return _build(
        ErrorCode.FORBIDDEN,
        message,
        operation,
        required_auth=credential,
        details={"credential": credential},
    )


def rate_limited(
    message: str, operation: str, *, retry_after_seconds: float | None = None
) -> AppError:
    details = (
        {"retry_after_seconds": retry_after_seconds} if retry_after_seconds is not None else None
    )
    return _build(ErrorCode.RATE_LIMITED, message, operation, details=details)


def upstream_timeout(message: str, operation: str) -> AppError:
    return _build(ErrorCode.UPSTREAM_TIMEOUT, message, operation)


def upstream_unavailable(message: str, operation: str) -> AppError:
    return _build(ErrorCode.UPSTREAM_UNAVAILABLE, message, operation)


def upstream_schema_changed(message: str, operation: str, **details: Any) -> AppError:
    return _build(ErrorCode.UPSTREAM_SCHEMA_CHANGED, message, operation, details=details or None)


def internal_error(message: str, operation: str, *, correlation_id: str | None = None) -> AppError:
    error = _build(ErrorCode.INTERNAL_ERROR, message, operation)
    if correlation_id is None:
        return error
    return AppError(
        code=error.code,
        message=error.message,
        operation=error.operation,
        action=error.action,
        correlation_id=correlation_id,
    )


__all__ = [
    "AppError",
    "ErrorAction",
    "ErrorCode",
    "ambiguous_entity",
    "authentication_rejected",
    "forbidden",
    "internal_error",
    "invalid_input",
    "missing_authentication",
    "not_found",
    "rate_limited",
    "upstream_schema_changed",
    "upstream_timeout",
    "upstream_unavailable",
]
