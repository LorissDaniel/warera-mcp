"""Pure parsing and canonicalization helpers for game configuration snapshots."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping


def content_fingerprint(payload: Mapping[str, object]) -> str:
    """Hash canonical decoded JSON; the value is local provenance, not a game revision."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def finite_number(value: object, *, integral: bool = False) -> int | float | None:
    """Accept JSON numbers only; booleans, non-finite values and fractions for integers fail."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if integral and not float(value).is_integer():
        return None
    return int(value) if integral else value


__all__ = ["content_fingerprint", "finite_number"]
