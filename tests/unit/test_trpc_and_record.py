"""tRPC envelope unwrapping and Record accessor behaviour."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from warera_mcp.warera.errors import WareraAPIError, WareraSchemaError
from warera_mcp.warera.schemas import (
    Record,
    extract_ident,
    parse_timestamp,
    parse_trpc_envelope,
    record_of,
)


def test_unwraps_plain_result_data() -> None:
    assert parse_trpc_envelope({"result": {"data": {"a": 1}}}) == {"a": 1}


def test_unwraps_superjson_result_data() -> None:
    payload = {"result": {"data": {"json": [1, 2], "meta": {"values": {}}}}}
    assert parse_trpc_envelope(payload) == [1, 2]


def test_surfaces_top_level_error_envelope() -> None:
    payload = {"error": {"json": {"message": "nope", "data": {"code": "FORBIDDEN"}}}}
    with pytest.raises(WareraAPIError) as excinfo:
        parse_trpc_envelope(payload)
    assert excinfo.value.code == "FORBIDDEN"


def test_surfaces_error_inside_result() -> None:
    payload = {"result": {"error": {"data": {"code": "BAD_REQUEST", "httpStatus": 400}}}}
    with pytest.raises(WareraAPIError) as excinfo:
        parse_trpc_envelope(payload)
    assert excinfo.value.code == "BAD_REQUEST"
    assert excinfo.value.http_status == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"nope": 1},
        {"result": {}},
        {"result": {"meta": {}}},
        [],
        "not-json-object",
    ],
)
def test_malformed_envelopes_raise_schema_error(payload: object) -> None:
    with pytest.raises(WareraSchemaError):
        parse_trpc_envelope(payload)


def test_null_data_is_returned_not_invented() -> None:
    assert parse_trpc_envelope({"result": {"data": None}}) is None


def test_extract_ident_handles_strings_ints_and_embedded_objects() -> None:
    assert extract_ident("u1") == "u1"
    assert extract_ident(1234) == "1234"
    assert extract_ident({"_id": "co1", "name": "x"}) == "co1"
    assert extract_ident({"name": "x"}) is None
    assert extract_ident(None) is None
    assert extract_ident(True) is None


def test_parse_timestamp_marks_naive_values() -> None:
    aware, naive = parse_timestamp("2024-05-01T10:00:00Z")
    assert aware == datetime(2024, 5, 1, 10, 0, tzinfo=UTC)
    assert naive is False

    parsed, was_naive = parse_timestamp("2024-05-01T10:00:00")
    assert parsed == datetime(2024, 5, 1, 10, 0, tzinfo=UTC)
    assert was_naive is True

    assert parse_timestamp("not-a-date") == (None, False)
    assert parse_timestamp(None) == (None, False)


def test_record_collects_warnings_instead_of_fabricating_values() -> None:
    record = record_of({"_id": "co1", "workerCount": "many", "population": None})
    assert record.required_ident("_id") == "co1"
    assert record.integer("workerCount") is None
    assert record.integer("population") is None
    assert any("workerCount" in problem for problem in record.problems)
    assert not any("population" in problem for problem in record.problems)


def test_record_nested_access_does_not_crash_on_wrong_types() -> None:
    record = record_of({"a": 5, "b": "text", "c": [1, 2]})
    assert record.child("a") is None
    assert record.child("b") is None
    assert record.objects("c") == []
    assert record.strings("c") == ["1", "2"]


def test_record_strings_ignores_non_mapping_entries() -> None:
    record = Record({"items": ["a", 1, None, {"_id": "b"}]})
    assert record.strings("items") == ["a", "1"]


def test_record_required_ident_reports_missing_key() -> None:
    record = record_of({})
    assert record.required_ident("_id") is None
    assert any("missing required identifier" in problem for problem in record.problems)
