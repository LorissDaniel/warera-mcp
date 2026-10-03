"""Sanitized transport exceptions raised by the WarEra query client.

These exceptions are the only error surface the client exposes. They never
retain upstream response bodies, request headers, URLs with query strings, or
credential material: callers receive a status class, a safe message and at most
a machine-readable tRPC error code.
"""

from __future__ import annotations

from warera_mcp.auth.requirements import CredentialKind


class WareraError(Exception):
    """Base class for every sanitized upstream failure."""


class WareraTransportError(WareraError):
    """Network-level failure (connection, DNS, protocol)."""


class WareraTimeout(WareraTransportError):
    """The upstream request exceeded its deadline."""


class WareraConnectionError(WareraTransportError):
    """The upstream connection failed before a response was received."""


class WareraResponseTooLarge(WareraError):
    """The upstream response exceeded the configured byte budget."""

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(f"upstream response exceeded {limit_bytes} bytes")
        self.limit_bytes = limit_bytes


class WareraCircuitOpen(WareraError):
    """The per-host circuit breaker is open; requests fail fast."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("upstream temporarily unavailable (circuit open)")
        self.retry_after_seconds = retry_after_seconds


class WareraOverloaded(WareraError):
    """Too many upstream reads are already queued; the request was shed locally."""

    def __init__(self) -> None:
        super().__init__("too many upstream reads are queued")


class WareraHTTPError(WareraError):
    """A non-success HTTP status from the upstream host."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class WareraRateLimited(WareraHTTPError):
    """HTTP 429 with an optional server-provided retry delay."""

    def __init__(self, retry_after_seconds: float | None) -> None:
        super().__init__(429, "upstream rate limit reached")
        self.retry_after_seconds = retry_after_seconds


class WareraAuthError(WareraHTTPError):
    """HTTP 401/403 from an authenticated upstream call."""

    def __init__(self, status_code: int) -> None:
        label = "authentication rejected" if status_code == 401 else "access forbidden"
        super().__init__(status_code, label)


class WareraServerError(WareraHTTPError):
    """HTTP 5xx from the upstream host (retryable)."""


class WareraAPIError(WareraError):
    """A tRPC error envelope returned by the upstream procedure."""

    def __init__(self, code: str | None, http_status: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class WareraSchemaError(WareraError):
    """The upstream payload did not match the expected tRPC envelope."""


class WareraMissingCredential(WareraError):
    """The operation's credential requirement was not satisfied locally."""

    def __init__(self, required: tuple[CredentialKind, ...]) -> None:
        super().__init__("required WarEra credential was not supplied")
        self.required = required


__all__ = [
    "WareraAPIError",
    "WareraAuthError",
    "WareraCircuitOpen",
    "WareraConnectionError",
    "WareraError",
    "WareraHTTPError",
    "WareraMissingCredential",
    "WareraOverloaded",
    "WareraRateLimited",
    "WareraResponseTooLarge",
    "WareraSchemaError",
    "WareraServerError",
    "WareraTimeout",
    "WareraTransportError",
]
