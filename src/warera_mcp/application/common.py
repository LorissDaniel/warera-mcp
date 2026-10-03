"""Shared application-layer infrastructure.

This module centralises the pieces every semantic service needs:

* :class:`ServiceRuntime` — the dependencies a service is constructed with.
* :class:`UpstreamCaller` — the only place that turns an upstream operation name
  into a client call, and the only place that maps sanitized transport failures
  into the canonical :class:`~warera_mcp.errors.AppError` taxonomy.
* bounded fan-out and conservative composite-timestamp helpers.

Services never touch :mod:`httpx` and never import the MCP SDK, which keeps them
independently testable against a mock transport.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeVar

from warera_mcp import errors as app_errors
from warera_mcp.auth.credentials import CredentialError, PlayerRequestContext
from warera_mcp.auth.warnings import CLEARTEXT_CREDENTIAL_WARNING
from warera_mcp.config import Settings
from warera_mcp.domain.recipes import NullRecipeCatalog, RecipeCatalog
from warera_mcp.observability.logging import StructuredLogger, make_logger
from warera_mcp.observability.metrics import Metrics, NullMetrics
from warera_mcp.observability.tracing import NullTracer, Tracer
from warera_mcp.warera.client import UpstreamRead, WareraQueryClient
from warera_mcp.warera.errors import (
    WareraAPIError,
    WareraAuthError,
    WareraCircuitOpen,
    WareraError,
    WareraHTTPError,
    WareraMissingCredential,
    WareraRateLimited,
    WareraResponseTooLarge,
    WareraSchemaError,
    WareraServerError,
    WareraTimeout,
)
from warera_mcp.warera.procedures import ProcedureParamError, get_procedure

T = TypeVar("T")

#: tRPC error-code fragments and the canonical error they imply.
_API_CODE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("NOT_FOUND",), "not_found"),
    (("UNAUTHORIZED",), "unauthorized"),
    (("FORBIDDEN",), "forbidden"),
    (("BAD_REQUEST", "PARSE_ERROR", "VALIDATION", "UNPROCESSABLE"), "invalid_input"),
    (("TOO_MANY_REQUESTS",), "rate_limited"),
    (("TIMEOUT", "GATEWAY"), "unavailable"),
)


@dataclass(slots=True)
class ServiceRuntime:
    """Dependencies shared by all semantic services."""

    client: WareraQueryClient
    settings: Settings
    metrics: Metrics = field(default_factory=NullMetrics)
    tracer: Tracer = field(default_factory=NullTracer)
    recipes: RecipeCatalog = field(default_factory=NullRecipeCatalog)
    logger: StructuredLogger | None = None

    def log(self) -> StructuredLogger:
        return self.logger or make_logger("warera_mcp.application")

    @property
    def max_fanout(self) -> int:
        return self.settings.max_fanout


def composite_observed_at(reads: Iterable[UpstreamRead]) -> datetime:
    """Return the *oldest* observation time in a composite read.

    Using the oldest source keeps ``observed_at`` from overstating freshness when
    part of a composite came from a warm cache.
    """
    timestamps = [read.observed_at for read in reads]
    if not timestamps:
        return datetime.now(UTC)
    return min(timestamps)


async def bounded_parallel(
    awaitables: Iterable[Awaitable[T]],
    *,
    limit: int,
) -> list[T | BaseException]:
    """Run awaitables concurrently under a hard semaphore, collecting outcomes."""
    semaphore = asyncio.Semaphore(max(1, limit))

    async def _run(awaitable: Awaitable[T]) -> T:
        async with semaphore:
            return await awaitable

    return list(await asyncio.gather(*(_run(item) for item in awaitables), return_exceptions=True))


async def bounded_try_reads(
    awaitables: Iterable[Awaitable[UpstreamRead | app_errors.AppError]],
    *,
    limit: int,
) -> list[UpstreamRead | app_errors.AppError | BaseException]:
    """Bounded fan-out specialised for :meth:`UpstreamCaller.try_read`.

    Services only ever fan out over tolerant reads, so a monomorphic helper keeps
    call sites readable and avoids generic inference having to guess the union.
    """
    return await bounded_parallel(awaitables, limit=limit)


def credential_kinds(context: PlayerRequestContext | None) -> tuple[str, ...]:
    if context is None:
        return ()
    return tuple(sorted(kind.value for kind in context.available_kinds()))


def map_credential_error(error: CredentialError, operation: str) -> app_errors.AppError:
    """Convert sanitized credential-input failures into an actionable error."""
    return app_errors.invalid_input(str(error), operation, field="player_context")


def map_upstream_error(
    error: WareraError,
    *,
    operation: str,
    procedure: str,
    credentials: PlayerRequestContext | None,
) -> app_errors.AppError:
    """Map a sanitized transport failure to the canonical error taxonomy."""
    available = credential_kinds(credentials)

    if isinstance(error, WareraMissingCredential):
        required = tuple(kind.value for kind in error.required)
        missing = tuple(kind for kind in required if kind not in available)
        return app_errors.missing_authentication(
            f"operation '{operation}' requires {' or '.join(required)}",
            operation,
            required=required,
            available=available,
            missing=missing,
            user_message=(
                f"{CLEARTEXT_CREDENTIAL_WARNING} If you accept this handling, provide "
                f"{' or '.join(required)} via player_context; otherwise request only public "
                "information."
            ),
        )

    if isinstance(error, WareraAuthError):
        kind = next(iter(available), "UNKNOWN")
        if error.status_code == 403:
            return app_errors.forbidden(
                f"the supplied {kind} credential is not authorized for '{operation}'",
                operation,
                credential=kind,
            )
        return app_errors.authentication_rejected(
            f"the supplied {kind} credential was rejected or expired", operation, credential=kind
        )

    if isinstance(error, WareraRateLimited):
        return app_errors.rate_limited(
            "WarEra rate limit reached; retry shortly",
            operation,
            retry_after_seconds=error.retry_after_seconds,
        )
    if isinstance(error, WareraTimeout):
        return app_errors.upstream_timeout("the WarEra request timed out", operation)
    if isinstance(error, (WareraServerError, WareraCircuitOpen)):
        return app_errors.upstream_unavailable("WarEra is temporarily unavailable", operation)
    if isinstance(error, WareraResponseTooLarge):
        return app_errors.upstream_schema_changed(
            "the WarEra response exceeded the configured size budget",
            operation,
            reason="response_too_large",
        )
    if isinstance(error, WareraSchemaError):
        return app_errors.upstream_schema_changed(
            "the WarEra response shape was not recognized", operation, procedure=procedure
        )
    if isinstance(error, WareraAPIError):
        return _map_api_error(error, operation=operation, procedure=procedure)
    if isinstance(error, WareraHTTPError):
        if error.status_code == 404:
            return app_errors.not_found(
                f"WarEra has no record for this {operation} request", operation
            )
        if error.status_code in (400, 422):
            return app_errors.invalid_input("WarEra rejected the request parameters", operation)
        return app_errors.upstream_unavailable(
            f"WarEra request failed ({error.status_code})", operation
        )

    return app_errors.upstream_unavailable("the WarEra request failed", operation)


def _map_api_error(error: WareraAPIError, *, operation: str, procedure: str) -> app_errors.AppError:
    code = (error.code or "").upper()
    hint = "unknown"
    for fragments, mapped in _API_CODE_HINTS:
        if any(fragment in code for fragment in fragments):
            hint = mapped
            break
    details = {"procedure": procedure}
    if error.code:
        details["upstream_code"] = error.code

    if hint == "not_found":
        return app_errors.not_found("WarEra has no record matching this request", operation)
    if hint == "unauthorized":
        return app_errors.authentication_rejected(
            "the supplied WarEra credential was rejected", operation, credential="unspecified"
        )
    if hint == "forbidden":
        return app_errors.forbidden(
            "the supplied WarEra credential is not authorized for this operation",
            operation,
            credential="unspecified",
        )
    if hint == "invalid_input":
        return app_errors.invalid_input("WarEra rejected the request parameters", operation)
    if hint == "rate_limited":
        return app_errors.rate_limited("WarEra rate limit reached; retry shortly", operation)
    return app_errors.upstream_unavailable("the WarEra operation failed", operation)


class UpstreamCaller:
    """Turns reviewed operation names into client calls with canonical errors."""

    def __init__(self, runtime: ServiceRuntime) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> ServiceRuntime:
        return self._runtime

    async def read(
        self,
        operation: str,
        procedure: str,
        params: Mapping[str, object] | None = None,
        *,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> UpstreamRead:
        try:
            spec = get_procedure(procedure)
            return await self._runtime.client.query(
                spec, params, credentials=credentials, correlation_id=correlation_id
            )
        except ProcedureParamError as error:
            raise app_errors.invalid_input(str(error), operation) from None
        except WareraError as error:
            raise map_upstream_error(
                error, operation=operation, procedure=procedure, credentials=credentials
            ) from None

    async def try_read(
        self,
        operation: str,
        procedure: str,
        params: Mapping[str, object] | None = None,
        *,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> UpstreamRead | app_errors.AppError:
        """Read without raising, for composites that degrade gracefully."""
        try:
            return await self.read(
                operation,
                procedure,
                params,
                credentials=credentials,
                correlation_id=correlation_id,
            )
        except app_errors.AppError as error:
            return error


def first_error(
    results: Sequence[UpstreamRead | app_errors.AppError],
) -> app_errors.AppError | None:
    for result in results:
        if isinstance(result, app_errors.AppError):
            return result
    return None


def succeeded_reads(
    results: Sequence[UpstreamRead | app_errors.AppError],
) -> list[UpstreamRead]:
    return [result for result in results if isinstance(result, UpstreamRead)]


__all__ = [
    "ServiceRuntime",
    "UpstreamCaller",
    "bounded_parallel",
    "bounded_try_reads",
    "composite_observed_at",
    "credential_kinds",
    "first_error",
    "map_credential_error",
    "map_upstream_error",
    "succeeded_reads",
]
