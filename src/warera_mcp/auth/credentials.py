"""Request-scoped WarEra credential container.

Player credentials are ordinary request arguments in v1. This module makes that
contract explicit, validated and *leak-proof*:

* Values live in :class:`~pydantic.SecretStr` so ``repr()``/``str()`` never show
  them.
* :func:`parse_player_context` validates untrusted MCP input and converts every
  failure into a :class:`CredentialError` whose message never contains the
  submitted value. This matters because the MCP SDK renders Pydantic validation
  errors including ``input_value`` — so a secret must never reach a framework
  validation step (which is also why the MCP tool signature types
  ``player_context`` as ``Any``).
* Nothing here persists, logs, or caches a credential.

Layer note: the design sketch places ``PlayerRequestContext`` under
``mcp_server/``. It lives here because the upstream client — which must build
auth headers per request — sits below the MCP layer, and importing server code
from the transport layer would invert the dependency direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

from warera_mcp.auth.redaction import SecretRedactor, contains_control_characters
from warera_mcp.auth.requirements import CredentialKind

API_KEY_MAX_LENGTH = 1024
JWT_MAX_LENGTH = 8192
IDENTIFIER_MAX_LENGTH = 64

#: Fields accepted inside a ``player_context`` object.
PLAYER_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {"warera_user_id", "warera_username", "api_key", "jwt"}
)

_IDENTIFIER_FIELDS = ("warera_user_id", "warera_username")


class CredentialError(ValueError):
    """Sanitized credential-input error. Never contains submitted values."""


class PlayerRequestContext(BaseModel):
    """Credentials and identifiers available to one tool invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    warera_user_id: str | None = None
    warera_username: str | None = None
    api_key: SecretStr | None = None
    jwt: SecretStr | None = None

    @field_validator(*_IDENTIFIER_FIELDS)
    @classmethod
    def _validate_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > IDENTIFIER_MAX_LENGTH:
            raise ValueError("identifier is too long")
        if contains_control_characters(value):
            raise ValueError("identifier contains control characters")
        return value

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_secret(value, API_KEY_MAX_LENGTH)

    @field_validator("jwt")
    @classmethod
    def _validate_jwt(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_secret(value, JWT_MAX_LENGTH)

    # -- capability queries ---------------------------------------------------
    def available_kinds(self) -> frozenset[CredentialKind]:
        kinds: set[CredentialKind] = set()
        if self.api_key is not None:
            kinds.add(CredentialKind.API_KEY)
        if self.jwt is not None:
            kinds.add(CredentialKind.JWT)
        return frozenset(kinds)

    def credential_for(self, kind: CredentialKind) -> SecretStr | None:
        return self.api_key if kind is CredentialKind.API_KEY else self.jwt

    def auth_headers(self, kind: CredentialKind) -> dict[str, str]:
        """Build headers for exactly one credential kind.

        Only one credential is ever sent on a request, matching the verified
        per-endpoint behaviour (no assumed combination of key and JWT).
        """
        secret = self.credential_for(kind)
        if secret is None:
            raise CredentialError(f"credential {kind.value} is not available for this request")
        value = secret.get_secret_value()
        if kind is CredentialKind.API_KEY:
            return {"X-API-Key": value}
        return {"Cookie": f"jwt={value}"}

    def secret_values(self) -> tuple[str, ...]:
        values: list[str] = []
        for secret in (self.api_key, self.jwt):
            if secret is not None:
                values.append(secret.get_secret_value())
        return tuple(values)

    def redactor(self) -> SecretRedactor:
        return SecretRedactor(self.secret_values())

    @property
    def has_any_credential(self) -> bool:
        return self.api_key is not None or self.jwt is not None


def _validate_secret(value: SecretStr | None, max_length: int) -> SecretStr | None:
    if value is None:
        return None
    raw = value.get_secret_value()
    if not raw.strip():
        raise ValueError("credential value is blank")
    if len(raw) > max_length:
        raise ValueError("credential value is too long")
    if contains_control_characters(raw):
        raise ValueError("credential value contains control characters")
    return value


def _safe_validation_message(exc: ValidationError) -> str:
    """Render a Pydantic failure using only locations and error kinds."""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "player_context"
        parts.append(f"{location}: {error.get('type', 'invalid')}")
    return "invalid player_context (" + "; ".join(parts) + ")"


def parse_player_context(payload: Any) -> PlayerRequestContext:
    """Validate untrusted ``player_context`` input without echoing values.

    Raises :class:`CredentialError` with a sanitized message on any problem.
    """
    if payload is None:
        return PlayerRequestContext()
    if not isinstance(payload, Mapping):
        raise CredentialError("player_context must be an object with optional credential fields")

    unknown = set(payload) - PLAYER_CONTEXT_FIELDS
    if unknown:
        # Field *names* are not secret and make the error actionable.
        raise CredentialError(
            "player_context has unsupported fields: " + ", ".join(sorted(str(k) for k in unknown))
        )

    cleaned: dict[str, Any] = {}
    for key in PLAYER_CONTEXT_FIELDS:
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise CredentialError(f"player_context.{key} must be a string")
        cleaned[key] = value

    try:
        return PlayerRequestContext.model_validate(cleaned)
    except ValidationError as exc:
        raise CredentialError(_safe_validation_message(exc)) from None


__all__ = [
    "API_KEY_MAX_LENGTH",
    "IDENTIFIER_MAX_LENGTH",
    "JWT_MAX_LENGTH",
    "PLAYER_CONTEXT_FIELDS",
    "CredentialError",
    "PlayerRequestContext",
    "parse_player_context",
]
