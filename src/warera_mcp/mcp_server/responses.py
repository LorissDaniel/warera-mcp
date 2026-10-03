"""Rendering canonical tool results into MCP ``CallToolResult`` objects.

Every tool returns a compact structured object plus a short human-readable
summary. A hard output-byte budget is enforced here so a projection can never
become an unbounded payload, independent of the per-tool row caps.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent

from warera_mcp import errors as app_errors
from warera_mcp.domain.models import ToolResult


def enforce_output_budget(structured: dict[str, Any], *, operation: str, max_bytes: int) -> None:
    """Raise an actionable error when a structured payload exceeds the budget."""
    encoded = json.dumps(structured, separators=(",", ":"), ensure_ascii=False)
    size = len(encoded.encode("utf-8"))
    if size > max_bytes:
        raise app_errors.upstream_schema_changed(
            "the projected response exceeded the configured output budget",
            operation,
            reason="output_too_large",
            bytes=size,
            limit_bytes=max_bytes,
        )


def success_result(
    model: ToolResult,
    *,
    summary: str,
    operation: str,
    max_bytes: int,
) -> CallToolResult:
    """Build a successful MCP result from a canonical domain model."""
    structured = model.to_structured()
    enforce_output_budget(structured, operation=operation, max_bytes=max_bytes)
    return CallToolResult(
        content=[TextContent(type="text", text=summary)],
        structuredContent=structured,
        isError=False,
    )


def truncate_summary(text: str, *, limit: int = 300) -> str:
    """Keep human-readable summaries short and single-line."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"


__all__ = ["enforce_output_budget", "success_result", "truncate_summary"]
