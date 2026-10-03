"""Outbound resilience primitives: rate limiting, circuit breaking, retries.

These are pure, clock-injectable helpers so their behaviour can be unit-tested
without sleeping or touching the network. The client composes them; nothing
here knows about HTTP or WarEra semantics beyond the sanitized error classes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from warera_mcp.warera.errors import (
    WareraError,
    WareraRateLimited,
    WareraServerError,
    WareraTransportError,
)

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]
Random01 = Callable[[], float]


class TokenBucket:
    """Async token bucket used to smooth outbound request rate.

    The bucket is process-local; it bounds a single instance's egress and is
    deliberately not treated as a global quota (see the design's scaling notes).
    """

    def __init__(
        self,
        rate_per_second: float,
        burst: int,
        *,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = rate_per_second
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated_at = clock()
        self._clock = clock
        self._sleep = sleeper
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        return self._tokens

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._updated_at)
        if elapsed:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated_at = now

    def _take(self) -> float:
        """Consume one token, returning seconds to wait (0 when granted)."""
        self._refill(self._clock())
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        return (1.0 - self._tokens) / self._rate

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            wait = self._take()
        while wait > 0:
            await self._sleep(wait)
            async with self._lock:
                wait = self._take()


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-host consecutive-failure circuit breaker with a single half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Clock = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._probe_in_flight:
            return CircuitState.HALF_OPEN
        if self._clock() - self._opened_at >= self._cooldown:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def retry_after(self) -> float:
        """Seconds until the breaker would allow a probe (0 when closed)."""
        if self._opened_at is None:
            return 0.0
        remaining = self._cooldown - (self._clock() - self._opened_at)
        return max(0.0, remaining)

    def allow(self) -> bool:
        """Return ``True`` when a request may proceed, reserving a probe if half-open."""
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at < self._cooldown:
            return False
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def abandon_probe(self) -> None:
        """Release a reserved half-open probe that produced no verdict.

        A probe can end without success or failure (the caller was cancelled, or
        the request was shed locally). Without this, the reservation would never
        be cleared and the breaker would refuse traffic forever.
        """
        self._probe_in_flight = False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self._opened_at is not None or self._failures >= self._threshold:
            self._opened_at = self._clock()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded, jittered retry policy for safe GET queries only."""

    max_retries: int = 2
    base_backoff_seconds: float = 0.2
    max_backoff_seconds: float = 2.0
    max_retry_after_seconds: float = 10.0

    def delay_for(
        self,
        attempt: int,
        error: WareraError,
        *,
        random01: Random01,
    ) -> float | None:
        """Return the delay before retrying, or ``None`` to give up.

        ``attempt`` is 1-based. Authorization, validation and not-found errors
        are never retried; only transport failures, 429s and 5xx responses are.
        """
        if attempt > self.max_retries:
            return None
        if isinstance(error, WareraRateLimited):
            ceiling = self.max_retry_after_seconds
            server_delay = error.retry_after_seconds
            if server_delay is not None:
                return min(max(server_delay, 0.0), ceiling)
            return min(self._backoff(attempt, random01), ceiling)
        if isinstance(error, (WareraServerError, WareraTransportError)):
            return self._backoff(attempt, random01)
        return None

    def _backoff(self, attempt: int, random01: Random01) -> float:
        base: float = min(
            self.base_backoff_seconds * float(2 ** (attempt - 1)),
            self.max_backoff_seconds,
        )
        jittered: float = base * random01()
        return max(0.01, jittered)


__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "RetryPolicy",
    "TokenBucket",
]
