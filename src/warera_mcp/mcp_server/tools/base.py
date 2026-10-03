"""Shared tool-building primitives.

Keeping the input-bound annotations and read-only tool annotations in one place
guarantees that every tool advertises consistent limits, cursor handling and
read-only semantics — which is what the MCP client sees when choosing a tool.
"""

from __future__ import annotations

from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

#: Rejects every C0 control character (including CR/LF/TAB) and DEL. Applied to
#: identifier-shaped tool inputs so header/log injection is impossible even before
#: the value reaches URL encoding.
SAFE_TEXT_PATTERN = r"^[^\x00-\x1f\x7f]*$"

#: Every tool in this server is a read. Advertising that explicitly lets clients
#: and hosts apply the right safety policy without inspecting implementations.
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

OptionalIdentifier = Annotated[
    str | None,
    Field(
        default=None,
        max_length=64,
        pattern=SAFE_TEXT_PATTERN,
        description="Opaque WarEra identifier. Prefer this over a name when you have it.",
    ),
]

RequiredIdentifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=SAFE_TEXT_PATTERN,
        description="Opaque WarEra identifier.",
    ),
]

OpaqueCursor = Annotated[
    str | None,
    Field(
        default=None,
        max_length=512,
        pattern=SAFE_TEXT_PATTERN,
        description="Cursor from a previous page's `next_cursor`. Treat it as opaque.",
    ),
]

SmallLimit = Annotated[
    int,
    Field(ge=1, le=10, default=5, description="Maximum rows to return (1-10)."),
]

MediumLimit = Annotated[
    int,
    Field(ge=1, le=20, default=10, description="Maximum rows to return (1-20)."),
]

__all__ = [
    "READ_ONLY_ANNOTATIONS",
    "SAFE_TEXT_PATTERN",
    "MediumLimit",
    "OpaqueCursor",
    "OptionalIdentifier",
    "RequiredIdentifier",
    "SmallLimit",
]
