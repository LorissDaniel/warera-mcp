"""Observability with strict allowlist redaction.

Logs, metrics and traces are exported through small interfaces so the domain
and application layers never depend on a vendor SDK. Every structured-field key
and metric label must be allowlisted, which makes it structurally impossible to
log credentials, player identifiers, raw payloads or URL query strings by
accident.
"""

from __future__ import annotations

from warera_mcp.observability.logging import (
    StructuredLogger,
    configure_logging,
    get_logger,
    make_logger,
)
from warera_mcp.observability.metrics import (
    InMemoryMetrics,
    Metrics,
    NullMetrics,
    safe_labels,
)
from warera_mcp.observability.tracing import NullTracer, Span, Tracer

__all__ = [
    "InMemoryMetrics",
    "Metrics",
    "NullMetrics",
    "NullTracer",
    "Span",
    "StructuredLogger",
    "Tracer",
    "configure_logging",
    "get_logger",
    "make_logger",
    "safe_labels",
]
