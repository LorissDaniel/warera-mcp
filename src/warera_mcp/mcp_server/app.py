"""FastMCP server construction and lifespan wiring.

``create_server`` is the single composition root: it turns validated settings into
a configured MCP server whose lifespan owns exactly one HTTP client and one
public cache. The lifespan closes the client on shutdown so in-flight pooled
connections are released cleanly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from warera_mcp.application import ServiceRuntime, build_services
from warera_mcp.cache.public_ttl import PublicTtlCache
from warera_mcp.config import Settings, load_settings
from warera_mcp.mcp_server.context import AppRuntime
from warera_mcp.mcp_server.instructions import SERVER_INSTRUCTIONS
from warera_mcp.mcp_server.tools.registry import register_tools
from warera_mcp.observability.logging import configure_logging, make_logger
from warera_mcp.observability.metrics import InMemoryMetrics, Metrics
from warera_mcp.observability.tracing import NullTracer, Tracer
from warera_mcp.warera.client import WareraQueryClient


def create_runtime(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    metrics: Metrics | None = None,
    tracer: Tracer | None = None,
) -> AppRuntime:
    """Build the process-level dependency graph (no credentials involved)."""
    resolved_metrics = metrics or InMemoryMetrics()
    resolved_tracer = tracer or NullTracer()
    cache = PublicTtlCache(
        max_entries=settings.cache_max_entries,
        enabled=settings.cache_enabled,
    )
    logger = make_logger("warera_mcp.warera.client")
    client = WareraQueryClient(
        settings,
        cache=cache,
        transport=transport,
        metrics=resolved_metrics,
        logger=logger,
    )
    service_runtime = ServiceRuntime(
        client=client,
        settings=settings,
        metrics=resolved_metrics,
        tracer=resolved_tracer,
        logger=make_logger("warera_mcp.application"),
    )
    return AppRuntime(
        settings=settings,
        client=client,
        cache=cache,
        services=build_services(service_runtime),
        metrics=resolved_metrics,
        tracer=resolved_tracer,
    )


def _make_lifespan(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    metrics: Metrics | None = None,
    tracer: Tracer | None = None,
) -> object:
    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[AppRuntime]:
        runtime = create_runtime(settings, transport=transport, metrics=metrics, tracer=tracer)
        try:
            yield runtime
        finally:
            await runtime.client.aclose()

    return lifespan


def build_transport_security(settings: Settings) -> TransportSecuritySettings | None:
    """Host/Origin validation (DNS-rebinding protection) from ``trusted_hosts``.

    When hosts are configured, only those ``Host`` values -- and, if CORS is
    enabled, those origins -- are accepted. With nothing configured the SDK's own
    loopback default applies.
    """
    if not settings.trusted_hosts:
        return None
    hosts: list[str] = []
    for host in settings.trusted_hosts:
        hosts.append(host)
        if ":" not in host:
            # A Host header may or may not carry a port; accept both forms.
            hosts.append(f"{host}:*")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=list(settings.cors_allow_origins),
    )


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    metrics: Metrics | None = None,
    tracer: Tracer | None = None,
) -> FastMCP:
    """Construct the MCP server with the approved read-only tool inventory."""
    resolved = settings or load_settings()
    configure_logging(resolved.log_level, json_format=resolved.log_json)

    mcp = FastMCP(
        name=resolved.server_name,
        instructions=SERVER_INSTRUCTIONS,
        host=resolved.host,
        port=resolved.port,
        streamable_http_path=resolved.streamable_http_path,
        stateless_http=resolved.stateless_http,
        json_response=resolved.json_response,
        max_request_body_size=resolved.max_request_body_bytes,
        transport_security=build_transport_security(resolved),
        log_level=resolved.log_level,
        lifespan=_make_lifespan(  # type: ignore[arg-type]
            resolved, transport=transport, metrics=metrics, tracer=tracer
        ),
    )
    register_tools(mcp)
    return mcp


def iter_approved_operations() -> Iterator[str]:
    """Expose the read-only upstream allowlist (used by security tests/docs)."""
    from warera_mcp.warera.procedures import READ_ONLY_PROCEDURES

    yield from sorted(READ_ONLY_PROCEDURES)


__all__ = [
    "build_transport_security",
    "create_runtime",
    "create_server",
    "iter_approved_operations",
]
