"""Per-client request limiting for the remote transport.

This protects the service boundary (and the shared upstream quota) from a single
abusive client. It is deliberately simple and process-local: the upstream rate
budget is enforced separately by the query client's token bucket, and horizontal
scaling should be bounded at the platform level rather than assumed here.

The client key is the transport-level peer address. ``X-Forwarded-For`` is ignored
unless ``trusted_proxy_hops`` is set, because a client could otherwise spoof it.
Behind N trusted proxies the Nth entry from the right is used (see
``ClientRateLimitMiddleware._client_key``).
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
    """Bounded token buckets keyed by client.

    Capacity pressure is handled so that an attacker cannot reset a throttled
    client's state by flooding the table with fresh keys:

    * buckets that have fully refilled carry no information and are purged first;
    * if the table is still full, *new* keys share one overflow bucket instead of
      evicting existing (possibly throttled) clients.
    """

    OVERFLOW_KEY = "__overflow__"

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

    def __len__(self) -> int:
        return len(self._buckets)

    def _refilled(self, bucket: _Bucket, now: float) -> float:
        return min(self._capacity, bucket.tokens + max(0.0, now - bucket.updated_at) * self._rate)

    def _purge_idle(self, now: float) -> None:
        idle = [
            key
            for key, bucket in self._buckets.items()
            if key != self.OVERFLOW_KEY and self._refilled(bucket, now) >= self._capacity
        ]
        for key in idle:
            del self._buckets[key]

    def consume(self, key: str) -> float:
        """Return 0.0 when allowed, otherwise seconds to wait."""
        now = self._clock()
        if key not in self._buckets and len(self._buckets) >= self._max_clients:
            self._purge_idle(now)
            if len(self._buckets) >= self._max_clients:
                key = self.OVERFLOW_KEY
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(tokens=self._capacity - 1.0, updated_at=now)
            return 0.0
        bucket.tokens = self._refilled(bucket, now)
        bucket.updated_at = now
        self._buckets.move_to_end(key)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return 0.0
        return (1.0 - bucket.tokens) / self._rate


def _header_value(scope: dict[str, Any], name: bytes) -> str | None:
    for entry in scope.get("headers") or ():
        if isinstance(entry, (tuple, list)) and len(entry) == 2 and entry[0].lower() == name:
            return bytes(entry[1]).decode("latin-1")
    return None


class ClientRateLimitMiddleware:
    """Per-peer-address token bucket returning HTTP 429 with ``Retry-After``."""

    def __init__(
        self,
        app: Any,
        *,
        per_minute: int,
        burst: int,
        exempt_paths: Sequence[str] = (),
        trusted_proxy_hops: int = 0,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = _MAX_TRACKED_CLIENTS,
    ) -> None:
        self._app = app
        self._exempt = frozenset(exempt_paths)
        self._proxy_hops = max(0, trusted_proxy_hops)
        self._buckets = _LocalBuckets(
            per_minute=per_minute, burst=burst, clock=clock, max_clients=max_clients
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self._app(scope, receive, send)
            return
        key = self._client_key(scope, self._proxy_hops)
        wait = self._buckets.consume(key)
        if wait > 0:
            await self._reject(send, wait)
            return
        await self._app(scope, receive, send)

    @staticmethod
    def _client_key(scope: dict[str, Any], proxy_hops: int = 0) -> str:
        """Identify the caller.

        By default this is the transport peer. Behind ``proxy_hops`` trusted
        reverse proxies every connection shares the proxy's address, so the real
        client is read from ``X-Forwarded-For``: the entry ``proxy_hops`` places
        from the right, i.e. the one appended by the outermost trusted proxy.
        Entries to its left are client-supplied and ignored, so they cannot be
        spoofed to dodge the limit.
        """
        if proxy_hops > 0:
            raw = _header_value(scope, b"x-forwarded-for")
            if raw:
                parts = [part.strip() for part in raw.split(",") if part.strip()]
                if len(parts) >= proxy_hops:
                    return parts[-proxy_hops][:64]
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
