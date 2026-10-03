"""Response-hardening middleware.

The service returns JSON game data, never browser-rendered HTML, and never
personalised or credential-bearing content. These headers make that explicit and
prevent intermediaries from caching responses that contain a caller's data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
)


class SecurityHeadersMiddleware:
    """Add a fixed set of hardening headers to every HTTP response."""

    def __init__(
        self,
        app: Any,
        *,
        headers: Mapping[str, str] | None = None,
        extra: Sequence[tuple[bytes, bytes]] = DEFAULT_SECURITY_HEADERS,
    ) -> None:
        self._app = app
        merged: dict[bytes, bytes] = dict(extra)
        if headers:
            for name, value in headers.items():
                merged[name.lower().encode("latin-1")] = value.encode("latin-1")
        self._headers = tuple(merged.items())

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", ()))
                present = {key.lower() for key, _ in existing}
                for key, value in self._headers:
                    if key not in present:
                        existing.append((key, value))
                message = dict(message)
                message["headers"] = existing
            await send(message)

        await self._app(scope, receive, send_with_headers)


__all__ = ["DEFAULT_SECURITY_HEADERS", "SecurityHeadersMiddleware"]
