"""ASGI middleware for the remote Streamable HTTP transport.

These middlewares sit at the service boundary only. They never inspect WarEra
credentials and never modify tool payloads.
"""

from __future__ import annotations

from warera_mcp.middleware.client_auth import BearerTokenAuthMiddleware, extract_bearer_token
from warera_mcp.middleware.headers import DEFAULT_SECURITY_HEADERS, SecurityHeadersMiddleware
from warera_mcp.middleware.request_limits import ClientRateLimitMiddleware

__all__ = [
    "DEFAULT_SECURITY_HEADERS",
    "BearerTokenAuthMiddleware",
    "ClientRateLimitMiddleware",
    "SecurityHeadersMiddleware",
    "extract_bearer_token",
]
