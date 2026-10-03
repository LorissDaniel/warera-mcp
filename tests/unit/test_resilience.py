"""Resilience primitives: token bucket, circuit breaker, retry classification."""

from __future__ import annotations

import pytest

from warera_mcp.warera.errors import (
    WareraAPIError,
    WareraAuthError,
    WareraConnectionError,
    WareraRateLimited,
    WareraServerError,
    WareraTimeout,
)
from warera_mcp.warera.resilience import CircuitBreaker, CircuitState, RetryPolicy, TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_token_bucket_grants_burst_then_sleeps() -> None:
    clock = FakeClock()
    slept: list[float] = []

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    bucket = TokenBucket(1.0, 2, clock=clock, sleeper=sleeper)
    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()
    assert len(slept) == 1
    assert slept[0] == pytest.approx(1.0)


def test_token_bucket_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        TokenBucket(1, 0)


def test_circuit_breaker_opens_after_threshold_and_recovers() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=clock)

    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED

    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False
    assert breaker.retry_after() == pytest.approx(10.0)

    clock.advance(10)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow() is True
    # A single half-open probe is allowed; a second concurrent probe is not.
    assert breaker.allow() is False

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_half_open_probe_failure_reopens_the_circuit() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
    breaker.allow()
    breaker.record_failure()
    clock.advance(5)
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow() is False


@pytest.mark.parametrize(
    ("attempt", "error", "expected"),
    [
        (1, WareraAuthError(401), None),
        (1, WareraAPIError("BAD_REQUEST", 400, "bad"), None),
        (1, ValueError("nope"), None),
        (1, WareraTimeout("slow"), "backoff"),
        (1, WareraConnectionError("down"), "backoff"),
        (1, WareraServerError(503, "boom"), "backoff"),
        (1, WareraRateLimited(None), "backoff"),
        (1, WareraRateLimited(1.5), 1.5),
        (1, WareraRateLimited(999.0), 10.0),
    ],
)
def test_retry_policy_classification(
    attempt: int, error: Exception, expected: float | str | None
) -> None:
    policy = RetryPolicy(
        max_retries=3,
        base_backoff_seconds=0.2,
        max_backoff_seconds=2.0,
        max_retry_after_seconds=10.0,
    )
    delay = policy.delay_for(attempt, error, random01=lambda: 0.5)  # type: ignore[arg-type]
    if expected == "backoff":
        assert delay is not None and 0 < delay <= 2.0
    else:
        assert delay == expected


def test_retry_policy_stops_after_max_retries() -> None:
    policy = RetryPolicy(max_retries=2)
    assert policy.delay_for(3, WareraServerError(500, "x"), random01=lambda: 1.0) is None


def test_backoff_is_jittered_and_never_zero() -> None:
    policy = RetryPolicy(base_backoff_seconds=1.0, max_backoff_seconds=8.0)
    assert policy.delay_for(1, WareraServerError(500, "x"), random01=lambda: 0.0) == 0.01
    assert policy.delay_for(1, WareraServerError(500, "x"), random01=lambda: 1.0) == 1.0
    assert policy.delay_for(2, WareraServerError(500, "x"), random01=lambda: 1.0) == 2.0
