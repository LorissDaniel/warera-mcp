"""Transport wiring: stdio for local development, Streamable HTTP for remote use.

The remote transport is stateless and is wrapped with the service-boundary
middlewares from :mod:`warera_mcp.middleware`. A local-only health endpoint is
added; it performs no upstream call and requires no credential.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

from warera_mcp.config import Settings
from warera_mcp.middleware.client_auth import BearerTokenAuthMiddleware
from warera_mcp.middleware.headers import SecurityHeadersMiddleware
from warera_mcp.middleware.request_limits import ClientRateLimitMiddleware
from warera_mcp.observability.logging import make_logger

_HEALTH_BODY = json.dumps({"status": "ok"}).encode("utf-8")


async def _health_endpoint(_request: Any) -> Any:
    from starlette.responses import Response

    return Response(content=_HEALTH_BODY, media_type="application/json")


def build_http_app(mcp: FastMCP, settings: Settings) -> Any:
    """Return the remote ASGI app with health route and boundary middleware."""
    app: Any = mcp.streamable_http_app()
    app.router.routes.insert(
        0, Route(settings.health_path, endpoint=_health_endpoint, methods=["GET"])
    )

    exempt = (settings.health_path,)
    if settings.require_client_auth:
        app = BearerTokenAuthMiddleware(
            app,
            tokens=[token.get_secret_value() for token in settings.client_auth_tokens],
            exempt_paths=exempt,
        )
    app = ClientRateLimitMiddleware(
        app,
        per_minute=settings.client_rate_limit_per_minute,
        burst=settings.client_rate_limit_burst,
        exempt_paths=exempt,
        trusted_proxy_hops=settings.trusted_proxy_hops,
    )
    app = SecurityHeadersMiddleware(app)
    if settings.enable_cors and settings.cors_allow_origins:
        app = CORSMiddleware(
            app,
            allow_origins=list(settings.cors_allow_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "authorization",
                "content-type",
                "mcp-protocol-version",
                "mcp-session-id",
            ],
            expose_headers=["mcp-session-id"],
            allow_credentials=False,
        )
    return app


def run_stdio(mcp: FastMCP) -> None:
    """Serve over stdio for local development."""
    mcp.run(transport="stdio")


def run_streamable_http(mcp: FastMCP, settings: Settings) -> None:
    """Serve over stateless Streamable HTTP using uvicorn."""
    import uvicorn

    log = make_logger("warera_mcp.transport")
    for notice in settings.exposure_warnings():
        log.warning(notice)

    uvicorn.run(
        build_http_app(mcp, settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


__all__ = ["build_http_app", "run_stdio", "run_streamable_http"]
