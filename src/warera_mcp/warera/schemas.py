"""Tolerant parsing of upstream WarEra payloads.

The upstream API is unofficial and its shapes drift. Rather than trusting raw
JSON, this module provides:

* :func:`parse_trpc_envelope` — unwraps the tRPC result envelope and converts
  upstream error envelopes into sanitized exceptions.
* :class:`Record` — a small typed accessor that extracts canonical fields while
  recording *warnings* for missing/invalid important fields instead of silently
  fabricating zeroes or empty strings.

No accessor echoes arbitrary upstream values into warnings, so free-form game
text cannot become part of an error message.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from warera_mcp.warera.errors import WareraAPIError, WareraSchemaError

_MISSING = object()


# --------------------------------------------------------------------------- helpers
def as_mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def as_sequence(value: object) -> Sequence[Any] | None:
    if isinstance(value, (list, tuple)):
        return value
    return None


def coerce_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def coerce_int(value: object) -> int | None:
    number = coerce_float(value)
    if number is None:
        return None
    if number != int(number):
        return None
    return int(number)


def coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def extract_ident(value: object) -> str | None:
    """Extract an opaque identifier from a string or an embedded object.

    WarEra sometimes returns a bare ID string and sometimes ``{"_id": ..., ...}``.
    Numeric-looking IDs are never coerced to numbers; integers are stringified
    only to preserve the exact value.
    """
    if isinstance(value, Mapping):
        for key in ("_id", "id"):
            if key in value:
                return extract_ident(value[key])
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, int):
        return str(value)
    return None


def extract_name(value: object) -> str | None:
    """Extract a display name from either a bare string or an object."""
    if isinstance(value, Mapping):
        for key in ("name", "username", "title"):
            if key in value:
                return coerce_str(value[key])
        return None
    return coerce_str(value)


def parse_timestamp(value: object) -> tuple[datetime | None, bool]:
    """Parse an ISO-8601 timestamp into an aware UTC datetime.

    Returns ``(datetime, was_naive)``. A naive source value is interpreted as
    UTC (preserving the numeric instant) and flagged so the caller can warn
    instead of inventing a timezone.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC), True
        return value.astimezone(UTC), False
    if not isinstance(value, str):
        return None, False
    text = value.strip()
    if not text:
        return None, False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC), True
    return parsed.astimezone(UTC), False


# --------------------------------------------------------------------------- record
@dataclass
class Record:
    """Typed, warning-collecting view over one upstream object."""

    data: Mapping[str, Any]
    path: str = "$"
    problems: list[str] = field(default_factory=list)

    # -- structure -----------------------------------------------------------
    def child(self, key: str) -> Record | None:
        nested = as_mapping(self.data.get(key))
        if nested is None:
            return None
        return Record(nested, path=f"{self.path}.{key}", problems=self.problems)

    def required_child(self, key: str) -> Record:
        child = self.child(key)
        if child is None:
            self._problem(key, "expected object")
            return Record({}, path=f"{self.path}.{key}", problems=self.problems)
        return child

    def objects(self, key: str) -> list[Record]:
        sequence = as_sequence(self.data.get(key))
        if sequence is None:
            return []
        return [
            Record(item, path=f"{self.path}.{key}[{index}]", problems=self.problems)
            for index, item in enumerate(sequence)
            if isinstance(item, Mapping)
        ]

    def strings(self, key: str) -> list[str]:
        sequence = as_sequence(self.data.get(key))
        if sequence is None:
            return []
        return [text for item in sequence if (text := coerce_str(item)) is not None]

    # -- scalars -------------------------------------------------------------
    def raw(self, key: str) -> Any:
        return self.data.get(key, _MISSING)

    def has(self, key: str) -> bool:
        return key in self.data and self.data[key] is not None

    def opt_str(self, key: str) -> str | None:
        return coerce_str(self.data.get(key))

    def str(self, key: str) -> str | None:
        value = coerce_str(self.data.get(key))
        if value is None and self.data.get(key) is not None:
            self._problem(key, "expected non-empty string")
        return value

    def ident(self, key: str) -> str | None:
        value = extract_ident(self.data.get(key))
        if value is None and self.data.get(key) is not None:
            self._problem(key, "expected identifier")
        return value

    def required_ident(self, key: str) -> str | None:
        value = self.ident(key)
        if value is None and self.data.get(key) is None:
            self._problem(key, "missing required identifier")
        return value

    def name(self, key: str) -> str | None:
        return extract_name(self.data.get(key))

    def num(self, key: str) -> float | None:
        value = coerce_float(self.data.get(key))
        if value is None and self.data.get(key) is not None:
            self._problem(key, "expected number")
        return value

    def required_num(self, key: str) -> float | None:
        value = self.num(key)
        if value is None:
            self._problem(key, "missing required number")
        return value

    def integer(self, key: str) -> int | None:
        value = coerce_int(self.data.get(key))
        if value is None and self.data.get(key) is not None:
            self._problem(key, "expected integer")
        return value

    def boolean(self, key: str) -> bool | None:
        value = coerce_bool(self.data.get(key))
        if value is None and self.data.get(key) is not None:
            self._problem(key, "expected boolean")
        return value

    def timestamp(self, key: str) -> datetime | None:
        raw = self.data.get(key)
        if raw is None:
            return None
        parsed, was_naive = parse_timestamp(raw)
        if parsed is None:
            self._problem(key, "expected ISO-8601 timestamp")
            return None
        if was_naive:
            self._problem(key, "timestamp had no timezone; interpreted as UTC")
        return parsed

    # -- diagnostics ---------------------------------------------------------
    def _problem(self, key: str, description: str) -> None:
        message = f"{self.path}.{key}: {description}"
        if message not in self.problems:
            self.problems.append(message)


def record_of(payload: object, path: str = "$") -> Record:
    """Build a :class:`Record` from a payload expected to be an object."""
    mapping = as_mapping(payload)
    if mapping is None:
        return Record({}, path=path, problems=[f"{path}: expected object"])
    return Record(mapping, path=path)


# --------------------------------------------------------------------------- tRPC
def _error_node_to_exception(node: object) -> WareraAPIError:
    """Convert a tRPC error node into a sanitized :class:`WareraAPIError`.

    Handles both the plain ``{"message", "code", "data"}`` node and the
    HTTP-transport ``{"json": {...}}`` wrapper used by tRPC v10.
    """
    payload = node
    if isinstance(node, Mapping) and isinstance(node.get("json"), Mapping):
        payload = node["json"]

    code: str | None = None
    http_status: int | None = None
    if isinstance(payload, Mapping):
        data = payload.get("data")
        detail = data if isinstance(data, Mapping) else payload
        raw_code = detail.get("code")
        if raw_code is None:
            raw_code = payload.get("code")
        if raw_code is not None:
            code = str(raw_code)
        http_status = coerce_int(detail.get("httpStatus"))
    message = f"upstream procedure failed ({code})" if code else "upstream procedure failed"
    return WareraAPIError(code=code, http_status=http_status, message=message)


def _unwrap_superjson(value: object) -> object:
    """Unwrap a ``{json, meta}`` superjson payload when present."""
    mapping = as_mapping(value)
    if mapping is not None and set(mapping) <= {"json", "meta"} and "json" in mapping:
        return mapping["json"]
    return value


def extract_trpc_error(payload: object) -> WareraAPIError | None:
    """Return the tRPC error encoded in *payload*, or ``None`` when absent."""
    if not isinstance(payload, Mapping):
        return None
    if "error" in payload and payload["error"] is not None:
        return _error_node_to_exception(payload["error"])
    result = payload.get("result")
    if isinstance(result, Mapping) and result.get("error") is not None:
        return _error_node_to_exception(result["error"])
    return None


def parse_trpc_envelope(payload: object) -> object:
    """Extract ``result.data`` from a tRPC HTTP response payload.

    Supports both the plain ``{"result": {"data": ...}}`` shape and the
    ``{"result": {"data": {"json": ...}}}`` superjson variant, and surfaces
    error envelopes as :class:`WareraAPIError`.
    """
    if not isinstance(payload, Mapping):
        raise WareraSchemaError("upstream response was not a JSON object")
    error = extract_trpc_error(payload)
    if error is not None:
        raise error

    result = payload.get("result", _MISSING)
    if result is _MISSING:
        raise WareraSchemaError("upstream response had no result envelope")
    if isinstance(result, Mapping) and "data" in result:
        return _unwrap_superjson(result["data"])
    raise WareraSchemaError("upstream result envelope had no data")


__all__ = [
    "Record",
    "as_mapping",
    "as_sequence",
    "coerce_bool",
    "coerce_float",
    "coerce_int",
    "coerce_str",
    "extract_ident",
    "extract_name",
    "extract_trpc_error",
    "parse_timestamp",
    "parse_trpc_envelope",
    "record_of",
]
