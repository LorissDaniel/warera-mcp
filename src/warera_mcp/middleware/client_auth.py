"""MCP client authentication middleware (distinct from WarEra credentials).

MCP client authentication answers "may this client call the server at all?".
WarEra player credentials answer "whose data may this tool read?". Conflating
them would be a security bug, so this middleware only ever inspects the MCP
``Authorization: Bearer`` header and never touches ``player_context``.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any

ASGIApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]],
    Awaitable[None],
]

_UNAUTHORIZED_BODY = json.dumps(
    {
        "error": {
            "code": "UNAUTHENTICATED",
            "message": "this MCP endpoint requires a valid client bearer token",
        }
    }
).encode("utf-8")


def _header(scope: dict[str, Any], name: bytes) -> bytes | None:
    headers = scope.get("headers")
    if not isinstance(headers, (list, tuple)):
        return None
    for entry in headers:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        key, value = entry
        if isinstance(key, bytes) and key.lower() == name and isinstance(value, bytes):
            return value
    return None


def extract_bearer_token(scope: dict[str, Any]) -> str | None:
    raw = _header(scope, b"authorization")
    if raw is None:
        return None
    text = raw.decode("latin-1").strip()
    scheme, _, token = text.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


class BearerTokenAuthMiddleware:
    """Pure-ASGI bearer-token gate for the remote (HTTP) transport."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        tokens: Iterable[str],
        exempt_paths: Sequence[str] = (),
    ) -> None:
        self._app = app
        self._tokens = tuple(token for token in tokens if token)
        self._exempt = frozenset(exempt_paths)

    @property
    def enabled(self) -> bool:
        return bool(self._tokens)

    def _authorized(self, token: str | None) -> bool:
        if token is None:
            return False
        return any(hmac.compare_digest(token, candidate) for candidate in self._tokens)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not self.enabled or scope.get("path") in self._exempt:
            await self._app(scope, receive, send)
            return
        if not self._authorized(extract_bearer_token(scope)):
            await self._reject(send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})


__all__ = ["ASGIApp", "BearerTokenAuthMiddleware", "extract_bearer_token"]
