"""MCP server layer: thin adapters over the semantic application services.

Nothing in this package performs HTTP, caching, or normalization. Tools parse
inputs, call one application service, and render a ``CallToolResult``.
"""

from __future__ import annotations

__all__: list[str] = []
