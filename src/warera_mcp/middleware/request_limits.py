"""Per-client request limiting for the remote transport.

This protects the service boundary (and the shared upstream quota) from a single
abusive client. It is deliberately simple and process-local: the upstream rate
budget is enforced separately by the query client's token bucket, and horizontal
scaling should be bounded at the platform level rather than assumed here.

The client key is the transport-level peer address. ``X-Forwarded-For`` is not
trusted, because a client could spoof it; a gateway that terminates TLS must be
configured to preserve the real peer address.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

_MAX_TRACKED_CLIENTS = 10_000


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class _LocalBuckets:
    """Bounded token buckets keyed by client, with deterministic eviction."""

    def __init__(
        self,
        *,
        per_minute: int,
        burst: int,
        clock: Callable[[], float],
        max_clients: int = _MAX_TRACKED_CLIENTS,
    ) -> None:
        self._rate = per_minute / 60.0
        self._capacity = float(max(1, burst))
        self._clock = clock
        self._max_clients = max_clients
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def consume(self, key: str) -> float:
        """Return 0.0 when allowed, otherwise seconds to wait."""
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self._max_clients:
                self._buckets.popitem(last=False)
            self._buckets[key] = _Bucket(tokens=self._capacity - 1.0, updated_at=now)
            return 0.0
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
        bucket.updated_at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            self._buckets.move_to_end(key)
            return 0.0
        return (1.0 - bucket.tokens) / self._rate


class ClientRateLimitMiddleware:
    """Per-peer-address token bucket returning HTTP 429 with ``Retry-After``."""

    def __init__(
        self,
        app: Any,
        *,
        per_minute: int,
        burst: int,
        exempt_paths: Sequence[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._exempt = frozenset(exempt_paths)
        self._buckets = _LocalBuckets(per_minute=per_minute, burst=burst, clock=clock)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self._app(scope, receive, send)
            return
        key = self._client_key(scope)
        wait = self._buckets.consume(key)
        if wait > 0:
            await self._reject(send, wait)
            return
        await self._app(scope, receive, send)

    @staticmethod
    def _client_key(scope: dict[str, Any]) -> str:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0])
        return "unknown"

    @staticmethod
    async def _reject(send: Any, wait_seconds: float) -> None:
        retry_after = max(1, int(wait_seconds + 0.999))
        body = json.dumps(
            {
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "too many requests from this client",
                    "retry_after_seconds": retry_after,
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(retry_after).encode()),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["ClientRateLimitMiddleware"]
