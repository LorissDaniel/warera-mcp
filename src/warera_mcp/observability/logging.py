"""Structured logging with an allowlist of safe fields.

The server never logs HTTP request/response bodies or headers, URL query
strings, credentials, or player identifiers. Rather than relying on reviewers,
:class:`StructuredLogger` drops any field whose key is not in
:data:`LOG_FIELD_ALLOWLIST`, and optionally scrubs known secret values from
string fields as a defence in depth.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from warera_mcp.auth.redaction import SecretRedactor

#: Only these keys may appear in a structured log record.
LOG_FIELD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "event",
        "tool",
        "procedure",
        "domain",
        "status",
        "duration_ms",
        "attempt",
        "cache",
        "rows",
        "partial",
        "error_code",
        "credential_class",
        "correlation_id",
        "fanout",
        "bytes",
        "state",
        "count",
    }
)


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter for one-record-per-line logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            # Exception types only; messages may embed sensitive data.
            payload["exception"] = getattr(record.exc_info[0], "__name__", "Exception")
        return json.dumps(payload, separators=(",", ":"), default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            rendered = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
            return f"{base} {rendered}"
        return base


#: Third-party loggers that record request URLs. ``httpx`` logs every request line at
#: INFO, including the query string, which carries player ids and search text.
#: They are pinned to WARNING regardless of the application log level.
_URL_LOGGING_LIBRARIES: tuple[str, ...] = ("httpx", "httpcore")


def configure_logging(
    level: str = "INFO",
    *,
    json_format: bool = True,
    stream: Any = None,
) -> None:
    """Configure the root handler once, idempotently."""
    handler = logging.StreamHandler(stream or sys.stderr)
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in _URL_LOGGING_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@dataclass(slots=True)
class StructuredLogger:
    """A stdlib logger wrapper that only records allowlisted, scrubbed fields."""

    logger: logging.Logger
    redactor: SecretRedactor = field(default_factory=SecretRedactor)

    def _log(self, level: int, event: str, fields: dict[str, Any]) -> None:
        if not self.logger.isEnabledFor(level):
            return
        safe: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in LOG_FIELD_ALLOWLIST or value is None:
                continue
            safe[key] = self.redactor.scrub(value) if isinstance(value, str) else value
        self.logger.log(level, self.redactor.scrub(event), extra={"fields": safe})

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields)

    def with_redactor(self, redactor: SecretRedactor) -> StructuredLogger:
        return StructuredLogger(logger=self.logger, redactor=redactor)


def make_logger(name: str, redactor: SecretRedactor | None = None) -> StructuredLogger:
    return StructuredLogger(logger=get_logger(name), redactor=redactor or SecretRedactor())


__all__ = [
    "LOG_FIELD_ALLOWLIST",
    "ConsoleFormatter",
    "JsonFormatter",
    "StructuredLogger",
    "configure_logging",
    "get_logger",
    "make_logger",
]
