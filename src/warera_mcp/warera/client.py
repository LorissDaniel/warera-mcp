"""Async, read-only WarEra tRPC query client.

Guarantees:

* **GET only.** The client exposes no method that can issue POST/PUT/PATCH/DELETE
  and only accepts a :class:`~warera_mcp.warera.procedures.ProcedureSpec` from the
  reviewed read-only allowlist — never a free-form procedure string.
* **One credential per request.** The credential is chosen per operation from
  the capability registry and materialised into a request-local header dict.
  Pending batches combine only calls with identical authentication headers;
  credential material is retained only for the lifetime of pending/active work.
* **Fixed origin.** The base URL is the validated WarEra host; redirects are not
  followed, which removes the SSRF pivot from tool arguments.
* **Sanitized failures.** Only the exception types in
  :mod:`warera_mcp.warera.errors` escape; response bodies, headers, and URLs with
  query strings are never retained.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.auth.requirements import CredentialKind, resolve_requirement
from warera_mcp.cache.public_ttl import CacheOutcome, PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.observability.logging import StructuredLogger, make_logger
from warera_mcp.observability.metrics import Metrics, NullMetrics
from warera_mcp.warera.batching import BatchCall, ReadBatcher
from warera_mcp.warera.errors import (
    WareraAPIError,
    WareraAuthError,
    WareraCircuitOpen,
    WareraConnectionError,
    WareraError,
    WareraHTTPError,
    WareraMissingCredential,
    WareraOverloaded,
    WareraRateLimited,
    WareraResponseTooLarge,
    WareraSchemaError,
    WareraServerError,
    WareraTimeout,
    WareraTransportError,
)
from warera_mcp.warera.procedures import ProcedureSpec
from warera_mcp.warera.resilience import CircuitBreaker, RetryPolicy, TokenBucket
from warera_mcp.warera.schemas import extract_trpc_error, parse_trpc_envelope

#: Upstream failures that indicate the host itself is unhealthy and therefore
#: count against the circuit breaker.
_AVAILABILITY_ERRORS: tuple[type[WareraError], ...] = (
    WareraServerError,
    WareraTransportError,
)


@dataclass(frozen=True, slots=True)
class UpstreamRead:
    """One logical upstream read, including freshness and cache metadata."""

    data: object
    observed_at: datetime
    cache_outcome: CacheOutcome
    attempts: int
    duration_ms: float


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _error_label(error: Exception) -> str:
    return type(error).__name__


class WareraQueryClient:
    """Async query-only client for the reviewed WarEra read surface."""

    def __init__(
        self,
        settings: Settings,
        *,
        cache: PublicTtlCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        metrics: Metrics | None = None,
        logger: StructuredLogger | None = None,
        clock: Callable[[], float] = time.monotonic,
        random01: Callable[[], float] = random.random,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._metrics = metrics or NullMetrics()
        self._logger = logger or make_logger("warera_mcp.warera.client")
        self._clock = clock
        self._random01 = random01
        self._sleep = sleeper

        self._http = httpx.AsyncClient(
            base_url=str(settings.warera_base_url).rstrip("/"),
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.read_timeout_seconds,
                write=settings.write_timeout_seconds,
                pool=settings.pool_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=settings.max_connections,
                max_keepalive_connections=settings.max_keepalive_connections,
            ),
            headers=self._default_headers(settings),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._limiter = TokenBucket(
            settings.outbound_rate_per_second,
            settings.outbound_rate_burst,
            clock=clock,
            sleeper=sleeper,
        )
        self._breaker = CircuitBreaker(
            failure_threshold=settings.circuit_failure_threshold,
            cooldown_seconds=settings.circuit_cooldown_seconds,
            clock=clock,
        )
        self._retry = RetryPolicy(
            max_retries=settings.max_retries,
            base_backoff_seconds=settings.retry_base_backoff_seconds,
            max_backoff_seconds=settings.retry_max_backoff_seconds,
            max_retry_after_seconds=settings.retry_max_retry_after_seconds,
        )
        self._pending = 0
        self._closed = False
        self._batcher = (
            ReadBatcher(
                window=settings.batch_window_seconds,
                max_size=settings.batch_max_size,
                capacity=settings.max_pending_requests,
                dispatch=self._dispatch_batch,
                fits=self._batch_fits,
            )
            if settings.batching_enabled
            else None
        )

    # ------------------------------------------------------------------ lifecycle
    @property
    def circuit_state(self) -> str:
        return self._breaker.state.value

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._batcher is not None:
                await self._batcher.aclose()
            await self._http.aclose()

    async def __aenter__(self) -> WareraQueryClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ---------------------------------------------------------------------- query
    async def query(
        self,
        spec: ProcedureSpec,
        params: Mapping[str, object] | None = None,
        *,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> UpstreamRead:
        """Run one reviewed read operation and return a bounded read result."""
        if self._closed:
            raise WareraOverloaded()
        normalized_params = dict(params or {})
        spec.validate_params(normalized_params)

        available = credentials.available_kinds() if credentials else frozenset()
        resolution = resolve_requirement(spec.auth, available)
        if not resolution.satisfied:
            raise WareraMissingCredential(resolution.required)

        auth_headers: dict[str, str] = {}
        auth_class = "PUBLIC"
        if resolution.selected is not None and credentials is not None:
            auth_headers = credentials.auth_headers(resolution.selected)
            auth_class = resolution.selected.value
        elif credentials is not None:
            # Public procedures may still be called on behalf of a user. Forward
            # that request's credential without making it process-global.
            if credentials.api_key is not None:
                auth_headers = credentials.auth_headers(CredentialKind.API_KEY)
                auth_class = CredentialKind.API_KEY.value
            elif credentials.jwt is not None:
                auth_headers = credentials.auth_headers(CredentialKind.JWT)
                auth_class = CredentialKind.JWT.value

        if spec.cacheable and self._cache is not None:

            async def loader() -> UpstreamRead:
                return await self._fetch(
                    spec, normalized_params, auth_headers, auth_class, correlation_id
                )

            key = self._cache.build_key(
                spec.name, normalized_params, schema_version=spec.schema_version
            )
            read, outcome = await self._cache.get_or_load(key, spec.ttl_seconds or 0.0, loader)
            self._metrics.increment(
                "warera_cache_lookups_total",
                labels={
                    "procedure": spec.name,
                    "domain": spec.domain.value,
                    "cache": outcome.value,
                },
            )
            return replace(read, cache_outcome=outcome)

        read = await self._fetch(spec, normalized_params, auth_headers, auth_class, correlation_id)
        return replace(read, cache_outcome=CacheOutcome.BYPASS)

    # -------------------------------------------------------------------- internals
    @staticmethod
    def _default_headers(settings: Settings) -> dict[str, str]:
        return {
            "User-Agent": settings.request_user_agent,
            "Accept": "application/json",
            "Origin": settings.warera_origin,
            "Referer": settings.warera_origin.rstrip("/") + "/",
        }

    async def _fetch(
        self,
        spec: ProcedureSpec,
        params: Mapping[str, object],
        auth_headers: Mapping[str, str],
        auth_class: str,
        correlation_id: str | None,
    ) -> UpstreamRead:
        if self._batcher is None and not self._breaker.allow():
            raise WareraCircuitOpen(self._breaker.retry_after())
        try:
            return await self._fetch_with_retries(
                spec, params, auth_headers, auth_class, correlation_id
            )
        except WareraError:
            raise
        except BaseException:
            # Cancellation (client disconnect, tool deadline) or an unexpected bug
            # produced no verdict about upstream health. Release a reserved
            # half-open probe so the breaker cannot wedge shut.
            if self._batcher is None:
                self._breaker.abandon_probe()
            raise

    async def _fetch_with_retries(
        self,
        spec: ProcedureSpec,
        params: Mapping[str, object],
        auth_headers: Mapping[str, str],
        auth_class: str,
        correlation_id: str | None,
    ) -> UpstreamRead:
        started = self._clock()
        attempt = 0
        last_error: Exception | None = None

        while True:
            attempt += 1
            try:
                if self._batcher is not None:
                    data = await self._attempt(spec, params, auth_headers, correlation_id, attempt)
                else:
                    await self._acquire_slot()
                    try:
                        data = await self._attempt(
                            spec, params, auth_headers, correlation_id, attempt
                        )
                    finally:
                        self._semaphore.release()
            except WareraError as error:
                last_error = error
                delay = self._retry.delay_for(attempt, error, random01=self._random01)
                self._logger.warning(
                    "upstream_attempt_failed",
                    procedure=spec.name,
                    domain=spec.domain.value,
                    status=_error_label(error),
                    attempt=attempt,
                    correlation_id=correlation_id,
                )
                if delay is None:
                    break
                await self._sleep(delay)
                continue

            duration_ms = (self._clock() - started) * 1000.0
            if self._batcher is None:
                self._breaker.record_success()
            self._observe(spec, auth_class, "success", duration_ms, attempt)
            return UpstreamRead(
                data=data,
                observed_at=datetime.now(UTC),
                cache_outcome=CacheOutcome.BYPASS,
                attempts=attempt,
                duration_ms=duration_ms,
            )

        # Exhausted retries (or a non-retryable failure).
        assert last_error is not None
        if self._batcher is None:
            if isinstance(last_error, _AVAILABILITY_ERRORS):
                self._breaker.record_failure()
            elif isinstance(last_error, WareraOverloaded):
                self._breaker.abandon_probe()
            else:
                self._breaker.record_success()
        duration_ms = (self._clock() - started) * 1000.0
        self._observe(spec, auth_class, _error_label(last_error), duration_ms, attempt)
        raise last_error

    async def _acquire_slot(self) -> None:
        """Wait for the outbound rate limit and a concurrency slot, with a bounded queue.

        Without a cap on waiters, a burst of tool calls would queue indefinitely
        behind the shared limiter and starve every other caller. Beyond the cap,
        reads are shed immediately with a retryable error.
        """
        if self._pending >= self._settings.max_pending_requests:
            raise WareraOverloaded
        self._pending += 1
        try:
            await self._limiter.acquire()
            await self._semaphore.acquire()
        finally:
            self._pending -= 1

    async def _attempt(
        self,
        spec: ProcedureSpec,
        params: Mapping[str, object],
        auth_headers: Mapping[str, str],
        correlation_id: str | None,
        attempt: int,
    ) -> object:
        payload = json.dumps(params, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
        if self._batcher is not None:
            return await self._batcher.submit(spec.name, payload, auth_headers)
        limit = self._settings.max_response_bytes
        try:
            self._metrics.increment("warera_http_requests_total")
            async with self._http.stream(
                "GET",
                f"/trpc/{spec.name}",
                params={"input": payload},
                headers=dict(auth_headers),
            ) as response:
                status = response.status_code
                headers = response.headers
                # Statuses that never carry a body we use are classified before a
                # single body byte is read.
                self._raise_for_status_without_body(status, headers)
                body = await self._read_limited(response, limit)
        except httpx.TimeoutException as exc:
            raise WareraTimeout("upstream request timed out") from exc
        except httpx.TransportError as exc:
            raise WareraConnectionError("upstream connection failed") from exc
        return self._handle_response(status, body)

    def _batch_url(self, calls: list[BatchCall]) -> httpx.URL:
        unique = {(call.procedure, call.payload): call for call in calls}
        indexed = {
            str(index): json.loads(call.payload) for index, call in enumerate(unique.values())
        }
        return self._http.base_url.join(
            "/trpc/" + ",".join(call.procedure for call in unique.values())
        ).copy_merge_params(
            {
                "batch": "1",
                "input": json.dumps(indexed, separators=(",", ":"), ensure_ascii=True),
            }
        )

    def _batch_fits(self, calls: list[BatchCall]) -> bool:
        return (
            len(str(self._batch_url(calls)).encode("ascii")) <= self._settings.batch_max_url_bytes
        )

    async def _dispatch_batch(self, calls: list[BatchCall]) -> list[object | WareraError]:
        # Admission and health accounting apply once per actual HTTP request,
        # including a half-open probe. Logical callers retain individual retries.
        if not self._breaker.allow():
            raise WareraCircuitOpen(self._breaker.retry_after())
        acquired = False
        try:
            await self._acquire_slot()
            acquired = True
            batch = len(calls) > 1
            if batch:
                url = self._batch_url(calls)
            else:
                url = self._http.base_url.join("/trpc/" + calls[0].procedure).copy_merge_params(
                    {"input": calls[0].payload}
                )
            self._metrics.increment("warera_http_requests_total")
            if batch:
                self._metrics.increment("warera_batches_total")
                self._metrics.observe("warera_batch_size", float(len(calls)))
            try:
                async with self._http.stream("GET", url, headers=calls[0].headers) as response:
                    status = response.status_code
                    self._raise_for_status_without_body(status, response.headers)
                    body = await self._read_limited(response, self._settings.max_response_bytes)
            except httpx.TimeoutException as exc:
                raise WareraTimeout("upstream request timed out") from exc
            except httpx.TransportError as exc:
                raise WareraConnectionError("upstream connection failed") from exc
            if not batch:
                data = self._handle_response(status, body)
                self._breaker.record_success()
                return [data]
            payload = self._decode_json(body)
            if not isinstance(payload, list) or len(payload) != len(calls):
                raise WareraSchemaError("upstream batch result count did not match its inputs")
            outcomes: list[object | WareraError] = []
            for item in payload:
                try:
                    data = parse_trpc_envelope(item)
                    if not 200 <= status < 300:
                        raise WareraHTTPError(status, f"upstream request failed ({status})")
                    outcomes.append(data)
                except WareraError as error:
                    outcomes.append(error)
            if all(
                isinstance(outcome, WareraAPIError) and (outcome.http_status or 0) >= 500
                for outcome in outcomes
            ):
                self._breaker.record_failure()
            else:
                self._breaker.record_success()
            return outcomes
        except WareraAPIError as error:
            if (error.http_status or 0) >= 500:
                self._breaker.record_failure()
            else:
                self._breaker.record_success()
            raise
        except _AVAILABILITY_ERRORS:
            self._breaker.record_failure()
            raise
        except WareraOverloaded:
            self._breaker.abandon_probe()
            raise
        except WareraError:
            self._breaker.record_success()
            raise
        except BaseException:
            self._breaker.abandon_probe()
            raise
        finally:
            if acquired:
                self._semaphore.release()

    @staticmethod
    def _raise_for_status_without_body(status: int, headers: Mapping[str, str]) -> None:
        if status == 429:
            raise WareraRateLimited(_retry_after_seconds(headers))
        if status in (401, 403):
            raise WareraAuthError(status)
        if status >= 500:
            raise WareraServerError(status, "upstream server error")

    @staticmethod
    async def _read_limited(response: httpx.Response, limit: int) -> bytes:
        """Read the (decoded) body, aborting as soon as it exceeds *limit* bytes.

        ``Content-Length`` is only a hint, and compressed bodies expand, so the cap
        is enforced on the bytes actually received. The connection is closed by the
        surrounding ``stream`` context when this raises.
        """
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    raise WareraResponseTooLarge(limit)
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise WareraResponseTooLarge(limit)
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _decode_json(body: bytes) -> object:
        try:
            return json.loads(body)
        except (ValueError, RecursionError) as exc:
            # ``ValueError`` covers invalid JSON and invalid UTF-8; deeply nested
            # documents raise ``RecursionError``.
            raise WareraSchemaError("upstream response was not valid JSON") from exc

    def _handle_response(self, status: int, body: bytes) -> object:
        if not 200 <= status < 300:
            # Preserve the tRPC error code when the body carries one; otherwise
            # fall back to a status-only error. Success envelopes on an error
            # status are deliberately ignored.
            payload: object = None
            try:
                payload = json.loads(body)
            except (ValueError, RecursionError):
                payload = None
            api_error = extract_trpc_error(payload) if payload is not None else None
            if api_error is not None:
                raise api_error
            raise WareraHTTPError(status, f"upstream request failed ({status})")

        return parse_trpc_envelope(self._decode_json(body))

    def _observe(
        self,
        spec: ProcedureSpec,
        auth_class: str,
        status: str,
        duration_ms: float,
        attempt: int,
    ) -> None:
        labels = {
            "procedure": spec.name,
            "domain": spec.domain.value,
            "status": status,
            "credential_class": auth_class,
        }
        self._metrics.increment("warera_requests_total", labels=labels)
        self._metrics.observe("warera_request_duration_ms", duration_ms, labels=labels)
        if attempt > 1:
            self._metrics.increment(
                "warera_retries_total",
                value=attempt - 1,
                labels={"procedure": spec.name, "domain": spec.domain.value},
            )


__all__ = ["UpstreamRead", "WareraQueryClient"]
