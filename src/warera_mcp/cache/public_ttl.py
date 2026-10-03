"""Bounded, process-local cache for *public* WarEra data only.

Design constraints enforced here:

* Authenticated/private responses are never stored. The cache is only reachable
  through :meth:`PublicTtlCache.get_or_load`, and the client refuses to route a
  non-public procedure through it.
* Keys are derived from the procedure name, normalized parameters and a schema
  version. Credential material can never appear in a key because credentials are
  not parameters of a public operation.
* Concurrent identical requests are coalesced (singleflight) so a cold cache
  cannot stampede the upstream API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

T = TypeVar("T")

Clock = Callable[[], float]


class CacheOutcome(StrEnum):
    """How a cache lookup was satisfied (used for metrics and diagnostics)."""

    HIT = "hit"
    MISS = "miss"
    JOIN = "join"
    BYPASS = "bypass"


@dataclass
class CacheStats:
    """Mutable counters exported through the metrics interface."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    joins: int = 0
    bypasses: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "joins": self.joins,
            "bypasses": self.bypasses,
        }


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float


class PublicTtlCache:
    """Async TTL + LRU cache with singleflight request coalescing."""

    def __init__(
        self,
        *,
        max_entries: int,
        enabled: bool = True,
        clock: Clock = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._enabled = enabled
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        self.stats = CacheStats()

    @staticmethod
    def build_key(
        procedure: str,
        params: Mapping[str, object],
        *,
        schema_version: int = 1,
    ) -> str:
        """Deterministic, credential-free cache key."""
        payload = json.dumps(
            {key: params[key] for key in sorted(params)},
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"{procedure}:v{schema_version}:{digest}"

    async def get_or_load(
        self,
        key: str,
        ttl_seconds: float,
        loader: Callable[[], Awaitable[T]],
    ) -> tuple[T, CacheOutcome]:
        """Return a cached value or load it, coalescing concurrent identical keys."""
        if not self._enabled or ttl_seconds <= 0:
            self.stats.bypasses += 1
            return await loader(), CacheOutcome.BYPASS

        now = self._clock()
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.expires_at > now:
                    self._entries.move_to_end(key)
                    self.stats.hits += 1
                    return entry.value, CacheOutcome.HIT
                del self._entries[key]
                self.stats.evictions += 1

            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = asyncio.ensure_future(loader())
                self._inflight[key] = inflight
                is_leader = True
            else:
                self.stats.joins += 1
                is_leader = False

        if not is_leader:
            return await inflight, CacheOutcome.JOIN

        try:
            value = await inflight
        except BaseException:
            async with self._lock:
                self._inflight.pop(key, None)
            raise

        async with self._lock:
            self._inflight.pop(key, None)
            self._store(key, value, ttl_seconds)
        self.stats.misses += 1
        return value, CacheOutcome.MISS

    def _store(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._entries[key] = _Entry(value=value, expires_at=self._clock() + ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1

    def invalidate_all(self) -> None:
        """Drop every cached entry (used on deploy/version bumps and in tests)."""
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["CacheOutcome", "CacheStats", "PublicTtlCache"]
