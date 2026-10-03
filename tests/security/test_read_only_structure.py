"""Structural read-only guarantees.

These tests do not exercise behaviour; they assert the *shape* of the codebase so
that a write capability cannot appear by accident:

* the registry is an explicit reviewed snapshot;
* the transport verb is GET and no write verb is used anywhere in the package;
* no executor / write-action module exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from warera_mcp.warera.client import WareraQueryClient
from warera_mcp.warera.procedures import ALLOWED_HTTP_METHOD

SRC = Path(__file__).resolve().parents[2] / "src" / "warera_mcp"

#: Any of these in production code would mean a write path exists.
WRITE_VERB_PATTERNS = (
    re.compile(r"\.post\("),
    re.compile(r"\.put\("),
    re.compile(r"\.patch\("),
    re.compile(r"\.delete\("),
    re.compile(r"httpx\.(post|put|patch|delete)\b"),
    re.compile(r"method\s*=\s*[\"'](POST|PUT|PATCH|DELETE)[\"']", re.IGNORECASE),
)

FORBIDDEN_MODULE_NAMES = ("executor", "executors", "write_client", "mutations", "actions")


def python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_package_has_sources() -> None:
    assert python_sources()


def test_no_write_verb_appears_in_production_code() -> None:
    offenders: list[str] = []
    for path in python_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in WRITE_VERB_PATTERNS:
            for match in pattern.finditer(text):
                offenders.append(f"{path.name}: {match.group(0)}")
    assert offenders == [], f"write verbs found in production code: {offenders}"


def test_transport_method_is_get_only() -> None:
    assert ALLOWED_HTTP_METHOD == "GET"


def test_client_exposes_no_write_methods() -> None:
    public_api = {name for name in dir(WareraQueryClient) if not name.startswith("_")}
    assert public_api == {"query", "aclose", "circuit_state"}


def test_no_executor_or_write_module_exists() -> None:
    names = {path.stem for path in python_sources()}
    assert names.isdisjoint(FORBIDDEN_MODULE_NAMES)


def test_package_ships_a_py_typed_marker() -> None:
    assert (SRC / "py.typed").is_file()
