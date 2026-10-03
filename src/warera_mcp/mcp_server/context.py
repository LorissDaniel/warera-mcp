"""Request-scoped context and the process-level runtime container.

* :class:`AppRuntime` is the immutable-per-process dependency graph produced by
  the server lifespan. It holds a connection-pooled HTTP client, the public
  cache and the semantic services — never credentials.
* ``ToolContext`` is the FastMCP context type tools receive. Credentials are
  *not* stored on it: they travel as tool arguments and are converted to a
  :class:`~warera_mcp.auth.credentials.PlayerRequestContext` inside the tool.

Layer note: the design sketch places ``PlayerRequestContext`` here too. It lives
in :mod:`warera_mcp.auth.credentials` because the upstream client layer must
build auth headers per request and must not import from the server layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import Context

from warera_mcp.application import Services
from warera_mcp.cache.public_ttl import PublicTtlCache
from warera_mcp.config import Settings
from warera_mcp.observability.metrics import Metrics
from warera_mcp.observability.tracing import Tracer
from warera_mcp.warera.client import WareraQueryClient


@dataclass(frozen=True, slots=True)
class AppRuntime:
    """Process-level dependencies shared by every tool invocation."""

    settings: Settings
    client: WareraQueryClient
    cache: PublicTtlCache
    services: Services
    metrics: Metrics
    tracer: Tracer


#: FastMCP context parameterised with this server's lifespan type.
ToolContext = Context[Any, AppRuntime, Any]


def runtime_of(ctx: ToolContext) -> AppRuntime:
    """Return the lifespan runtime for the current request."""
    runtime: AppRuntime = ctx.request_context.lifespan_context
    return runtime


def call_id(ctx: ToolContext) -> str:
    """Correlation id for this tool invocation (the MCP request id)."""
    return str(ctx.request_id)


__all__ = ["AppRuntime", "ToolContext", "call_id", "runtime_of"]
