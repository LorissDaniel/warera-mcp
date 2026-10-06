"""Rendering canonical tool results into MCP ``CallToolResult`` objects.

Every tool returns a compact structured object, a short human-readable summary,
and a JSON text copy of the same object for clients that only consume content.
The projection's byte budget applies before it is copied into text; the copy is
therefore bounded too, independently of the per-tool row caps.
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
        raise app_errors.output_too_large(
            "the projected response exceeded the configured output budget; "
            "request fewer rows or a narrower selection",
            operation,
            bytes=size,
            limit_bytes=max_bytes,
        )


def success_result(
    model: ToolResult,
    *,
    summary: str,
    operation: str,
    max_bytes: int,
    summary_limit: int = 300,
) -> CallToolResult:
    """Build a successful MCP result from a canonical domain model."""
    structured = model.to_structured()
    enforce_output_budget(structured, operation=operation, max_bytes=max_bytes)
    return CallToolResult(
        content=[
            TextContent(type="text", text=truncate_summary(summary, limit=summary_limit)),
            TextContent(
                type="text",
                text=json.dumps(structured, separators=(",", ":"), ensure_ascii=False),
            ),
        ],
        structuredContent=structured,
        isError=False,
    )


def quoted(name: str | None) -> str:
    """Render a player-controlled name as a quoted literal inside a summary.

    Quoting keeps game text visibly separate from the surrounding sentence so it
    reads as data, and stripping embedded quotes stops it from closing the literal.
    """
    return '"' + (name or "").replace('"', "'") + '"'


def truncate_summary(text: str, *, limit: int = 300) -> str:
    """Keep human-readable summaries short and single-line."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"


__all__ = ["enforce_output_budget", "quoted", "success_result", "truncate_summary"]
