"""In-process metrics with bounded label cardinality.

Labels are validated against an allowlist so player IDs, usernames, item codes,
cursor values and credentials can never become metric dimensions. The default
implementation is in-memory; deployments can swap in an OpenTelemetry-backed
:class:`Metrics` implementation without touching application code.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Only these label keys may be attached to a metric.
ALLOWED_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "tool",
        "procedure",
        "domain",
        "status",
        "cache",
        "error_code",
        "credential_class",
        "state",
    }
)

#: Values that would create unbounded cardinality are rejected outright.
_FORBIDDEN_LABEL_VALUES = frozenset({"", "unknown", "none", "null"})


class InvalidLabelError(ValueError):
    """Raised when a metric label would violate the cardinality policy."""


def safe_labels(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    """Validate and freeze labels into a hashable, policy-compliant tuple."""
    if not labels:
        return ()
    normalized: list[tuple[str, str]] = []
    for key, value in labels.items():
        if key not in ALLOWED_LABEL_KEYS:
            raise InvalidLabelError(f"metric label {key!r} is not allowlisted")
        text = "" if value is None else str(value)
        if text.lower() in _FORBIDDEN_LABEL_VALUES:
            continue
        if len(text) > 64:
            raise InvalidLabelError(f"metric label {key!r} exceeds 64 characters")
        normalized.append((key, text))
    return tuple(sorted(normalized))


@runtime_checkable
class Metrics(Protocol):
    """Minimal metrics surface used by the service."""

    def increment(
        self, name: str, value: int = 1, *, labels: Mapping[str, str] | None = None
    ) -> None: ...

    def observe(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None: ...


@dataclass
class NullMetrics:
    """No-op metrics implementation."""

    def increment(
        self, name: str, value: int = 1, *, labels: Mapping[str, str] | None = None
    ) -> None:
        return None

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        return None


@dataclass
class InMemoryMetrics:
    """Bounded in-memory counters and summaries for tests and diagnostics.

    Histograms keep count/sum/min/max rather than raw samples, which keeps memory
    bounded regardless of traffic.
    """

    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    summaries: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, float]] = field(
        default_factory=dict
    )

    def increment(
        self, name: str, value: int = 1, *, labels: Mapping[str, str] | None = None
    ) -> None:
        self.counters[(name, safe_labels(labels))] += value

    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        key = (name, safe_labels(labels))
        summary = self.summaries.get(key)
        if summary is None:
            self.summaries[key] = {"count": 1.0, "sum": value, "min": value, "max": value}
            return
        summary["count"] += 1
        summary["sum"] += value
        summary["min"] = min(summary["min"], value)
        summary["max"] = max(summary["max"], value)

    def snapshot(self) -> dict[str, object]:
        return {
            "counters": {
                f"{name}{dict(labels)}": value for (name, labels), value in self.counters.items()
            },
            "summaries": {
                f"{name}{dict(labels)}": dict(summary)
                for (name, labels), summary in self.summaries.items()
            },
        }


__all__ = [
    "ALLOWED_LABEL_KEYS",
    "InMemoryMetrics",
    "InvalidLabelError",
    "Metrics",
    "NullMetrics",
    "safe_labels",
]
