"""Metrics cardinality policy and redacting structured logging."""

from __future__ import annotations

import json
import logging

import pytest

from warera_mcp.auth.redaction import SecretRedactor
from warera_mcp.observability.logging import (
    LOG_FIELD_ALLOWLIST,
    JsonFormatter,
    StructuredLogger,
    configure_logging,
)
from warera_mcp.observability.metrics import (
    InMemoryMetrics,
    InvalidLabelError,
    NullMetrics,
    safe_labels,
)


def test_safe_labels_accepts_only_allowlisted_keys() -> None:
    assert safe_labels({"tool": "get_player"}) == (("tool", "get_player"),)
    with pytest.raises(InvalidLabelError):
        safe_labels({"user_id": "u1"})
    with pytest.raises(InvalidLabelError):
        safe_labels({"item_code": "iron"})


def test_safe_labels_rejects_oversized_values_and_drops_placeholder_values() -> None:
    with pytest.raises(InvalidLabelError):
        safe_labels({"tool": "x" * 65})
    assert safe_labels({"status": "", "tool": "unknown", "procedure": "p"}) == (("procedure", "p"),)


def test_safe_labels_is_order_independent() -> None:
    assert safe_labels({"tool": "a", "status": "ok"}) == safe_labels({"status": "ok", "tool": "a"})


def test_in_memory_metrics_count_and_summarise() -> None:
    metrics = InMemoryMetrics()
    metrics.increment("calls", labels={"tool": "get_player"})
    metrics.increment("calls", value=2, labels={"tool": "get_player"})
    metrics.observe("latency_ms", 10.0, labels={"tool": "get_player"})
    metrics.observe("latency_ms", 30.0, labels={"tool": "get_player"})

    snapshot = metrics.snapshot()
    assert snapshot["counters"]["calls{'tool': 'get_player'}"] == 3
    summary = snapshot["summaries"]["latency_ms{'tool': 'get_player'}"]
    assert summary == {"count": 2.0, "sum": 40.0, "min": 10.0, "max": 30.0}


def test_null_metrics_accepts_everything() -> None:
    metrics = NullMetrics()
    metrics.increment("x", labels={"tool": "y"})
    metrics.observe("x", 1.0, labels=None)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> _Capture:
    handler = _Capture()
    logger = logging.getLogger("warera_mcp.test")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return handler


def test_structured_logger_drops_non_allowlisted_fields(capture: _Capture) -> None:
    logger = StructuredLogger(logger=logging.getLogger("warera_mcp.test"))
    logger.info("tool_call", tool="get_player", user_id="u1", secret_value="leak")
    record = capture.records[-1]
    assert record.fields == {"tool": "get_player"}
    assert "user_id" not in record.fields
    assert LOG_FIELD_ALLOWLIST.isdisjoint({"secret_value"})


def test_structured_logger_scrubs_credential_values(capture: _Capture) -> None:
    secret = "wae_verysecretvalue"
    logger = StructuredLogger(
        logger=logging.getLogger("warera_mcp.test"), redactor=SecretRedactor([secret])
    )
    logger.error("upstream_failed", procedure="user.getUserLite", error_code=secret)
    record = capture.records[-1]
    assert secret not in json.dumps(record.fields)
    assert record.fields["error_code"] == "[redacted]"


def test_json_formatter_emits_one_json_object_per_record(capture: _Capture) -> None:
    logger = StructuredLogger(logger=logging.getLogger("warera_mcp.test"))
    logger.info("tool_call", tool="get_player", duration_ms=12.5)
    formatted = JsonFormatter().format(capture.records[-1])
    payload = json.loads(formatted)
    assert payload["event"] == "tool_call"
    assert payload["tool"] == "get_player"
    assert payload["duration_ms"] == 12.5
    assert payload["ts"].endswith("Z")


def test_configure_logging_is_idempotent() -> None:
    configure_logging("WARNING", json_format=True)
    configure_logging("INFO", json_format=False)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.INFO
