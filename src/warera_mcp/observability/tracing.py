"""Tracing seam over OpenTelemetry (optional dependency).

The service only ever sets a small allowlist of span attributes: tool and
procedure identifiers, status/auth class and duration. Credentials, full URLs,
query strings and raw inputs are never attached.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Only these span attributes may be set.
ALLOWED_SPAN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "mcp.tool",
        "warera.procedure",
        "warera.domain",
        "warera.status",
        "warera.auth_class",
        "warera.attempt",
        "warera.cache",
    }
)


@dataclass
class Span:
    """Mutable span handle; ignores non-allowlisted attributes."""

    name: str
    attributes: dict[str, object] = field(default_factory=dict)
    error: str | None = None

    def set_attribute(self, key: str, value: object) -> None:
        if key in ALLOWED_SPAN_ATTRIBUTES:
            self.attributes[key] = value

    def set_error(self, error_code: str) -> None:
        self.error = error_code


@runtime_checkable
class Tracer(Protocol):
    """Starts spans for a unit of work."""

    def start_span(
        self, name: str, *, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[Span]: ...


class NullTracer:
    """No-op tracer used when tracing is not configured."""

    @contextmanager
    def start_span(
        self, name: str, *, attributes: Mapping[str, object] | None = None
    ) -> Iterator[Span]:
        span = Span(name=name)
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        try:
            yield span
        finally:
            pass


__all__ = ["ALLOWED_SPAN_ATTRIBUTES", "NullTracer", "Span", "Tracer"]
