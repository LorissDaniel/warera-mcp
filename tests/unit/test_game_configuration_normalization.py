from __future__ import annotations

import math

from warera_mcp.domain.game_configuration_normalization import (
    content_fingerprint,
    finite_number,
)


def test_fingerprint_is_key_order_independent_and_content_sensitive() -> None:
    assert content_fingerprint({"a": 1, "b": {"x": 2}}) == content_fingerprint(
        {"b": {"x": 2}, "a": 1}
    )
    assert content_fingerprint({"a": 1}) != content_fingerprint({"a": 2})


def test_numeric_parser_keeps_zero_and_rejects_booleans_nonfinite_and_fractional_ints() -> None:
    assert finite_number(0) == 0
    assert finite_number(False) is None
    assert finite_number(math.nan) is None
    assert finite_number(math.inf) is None
    assert finite_number(1.5, integral=True) is None
    assert finite_number(2.0, integral=True) == 2
