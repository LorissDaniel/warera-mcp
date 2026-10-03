"""Request URLs (which carry player ids and search text) must never reach the logs."""

from __future__ import annotations

import io
import logging

import httpx

from warera_mcp.observability.logging import configure_logging


async def test_httpx_request_lines_are_not_logged_even_at_info_and_debug() -> None:
    for level in ("INFO", "DEBUG"):
        stream = io.StringIO()
        configure_logging(level, json_format=True, stream=stream)
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={}))
        async with httpx.AsyncClient(transport=transport, base_url="https://api2.warera.io") as c:
            await c.get("/trpc/search.searchUsers", params={"input": '{"searchText":"SecretName"}'})
        output = stream.getvalue()
        assert "SecretName" not in output
        assert "searchText" not in output
        assert "HTTP Request" not in output


def test_url_logging_libraries_are_pinned_to_warning() -> None:
    configure_logging("DEBUG", json_format=True, stream=io.StringIO())
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_application_loggers_still_honour_the_configured_level() -> None:
    stream = io.StringIO()
    configure_logging("INFO", json_format=True, stream=stream)
    logging.getLogger("warera_mcp.test").info("still visible")
    assert "still visible" in stream.getvalue()
