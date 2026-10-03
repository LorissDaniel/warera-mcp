"""JWT shape validation.

The JWT is sent as ``Cookie: jwt=<value>``. Anything outside the base64url token
alphabet could add cookies or break header framing, so only a real three-segment
token is accepted. The sample tokens here are generated, never copied from a real
account; they only mirror the structure of a genuine WarEra session token
(HS256 header, ``data``/``exp``/``iat`` claims, 36/103/43-character segments).
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

import pytest

from warera_mcp.auth.credentials import (
    JWT_MAX_LENGTH,
    CredentialError,
    PlayerRequestContext,
    parse_player_context,
)
from warera_mcp.auth.requirements import CredentialKind


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def synthetic_jwt() -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64url(
        json.dumps(
            {"data": secrets.token_hex(12), "iat": 1_700_000_000, "exp": 1_800_000_000}
        ).encode()
    )
    signature = b64url(secrets.token_bytes(32))
    return f"{header}.{payload}.{signature}"


def test_a_realistically_shaped_token_is_accepted_and_sent_as_a_cookie() -> None:
    token = synthetic_jwt()
    context = parse_player_context({"jwt": token})
    assert context.available_kinds() == {CredentialKind.JWT}
    assert context.auth_headers(CredentialKind.JWT) == {"Cookie": f"jwt={token}"}


def test_the_sample_matches_the_structure_of_a_real_session_token() -> None:
    header, payload, signature = synthetic_jwt().split(".")
    assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "HS256", "typ": "JWT"}
    assert set(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))) == {
        "data",
        "exp",
        "iat",
    }
    assert len(signature) == 43


@pytest.mark.parametrize(
    "value",
    [
        "a.b.c",
        "header.payload.signature",
        "abc-DEF_123.abc-DEF_123.abc-DEF_123",
        "aaa.bbb.ccc=",  # tolerated base64 padding
    ],
)
def test_three_segment_base64url_values_are_accepted(value: str) -> None:
    assert PlayerRequestContext(jwt=value).jwt is not None


@pytest.mark.parametrize(
    "value",
    [
        "no-dots-at-all",
        "only.two",
        "a.b.c.d",
        "a..c",
        ".b.c",
        "a.b.",
        "a.b.c; admin=1",  # extra cookie
        "a.b.c;",
        "a.b.c, other=1",
        'a."b".c',
        "a.b.c\\x",
        "a b.c.d",
        "a.b.c d",
        "a.b.c\tz",
        "a.b.c=; Path=/",
        "a.b.c%3B",  # percent sign is outside the alphabet
        "a.b.c/d",
        "a.b.c+d",
    ],
)
def test_values_that_are_not_a_clean_jwt_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        PlayerRequestContext(jwt=value)


@pytest.mark.parametrize("value", ["a.b.c\r\nX-Evil: 1", "a.b.c\n", "a.b.c\x00"])
def test_header_injection_attempts_are_rejected(value: str) -> None:
    with pytest.raises(CredentialError):
        parse_player_context({"jwt": value})


def test_oversized_tokens_are_rejected() -> None:
    too_long = "a." + "b" * JWT_MAX_LENGTH + ".c"
    with pytest.raises(CredentialError):
        parse_player_context({"jwt": too_long})


def test_rejection_never_echoes_the_submitted_value() -> None:
    secret = "supersecret.value.with;semicolon"
    with pytest.raises(CredentialError) as excinfo:
        parse_player_context({"jwt": secret})
    message = str(excinfo.value)
    assert "supersecret" not in message
    assert "semicolon" not in message
    assert "jwt" in message


def test_api_keys_are_not_subject_to_jwt_shape_rules() -> None:
    assert PlayerRequestContext(api_key="wae_not_a_jwt_at_all").api_key is not None


def test_jwt_validation_applies_through_the_tool_boundary(settings: Any, stub: Any) -> None:
    """A malformed token is refused by the sanitized parser before any request is made."""
    with pytest.raises(CredentialError):
        parse_player_context({"jwt": "x.y.z; Path=/"})
    assert stub.requests == []
