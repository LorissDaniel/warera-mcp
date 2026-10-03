"""Command-line entry point.

Two transports are supported and are configured explicitly rather than inferred:

* ``stdio`` (default) for local development and desktop MCP clients;
* ``streamable-http`` for a hosted, stateless deployment.

The WarEra host is never taken from the command line: it is fixed by
:mod:`warera_mcp.config` and guarded against override.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from warera_mcp import __version__
from warera_mcp.config import load_settings
from warera_mcp.mcp_server.app import create_server
from warera_mcp.mcp_server.transport import run_stdio, run_streamable_http


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warera-mcp",
        description="Standalone, strictly read-only MCP server for WarEra game data.",
    )
    parser.add_argument("--version", action="version", version=f"warera-mcp {__version__}")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport to serve (default: stdio).",
    )
    parser.add_argument("--host", default=None, help="Bind host for streamable-http.")
    parser.add_argument("--port", type=int, default=None, help="Bind port for streamable-http.")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="Logging level.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    overrides: dict[str, object] = {}
    if args.host is not None:
        overrides["host"] = args.host
    if args.port is not None:
        overrides["port"] = args.port
    if args.log_level is not None:
        overrides["log_level"] = args.log_level

    settings = load_settings(**overrides)
    server = create_server(settings)
    if args.transport == "stdio":
        run_stdio(server)
    else:
        run_streamable_http(server, settings)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
