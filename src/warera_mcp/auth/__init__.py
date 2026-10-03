"""Request-scoped authentication seams for WarEra credentials.

This package defines credential *kinds* and endpoint-level requirements. It
never stores, logs, echoes, or persists credential values, and it never infers
a credential "strength" hierarchy: an operation is satisfied only by the exact
credential set that was verified for it.
"""

from __future__ import annotations

from warera_mcp.auth.credentials import (
    CredentialError,
    PlayerRequestContext,
    parse_player_context,
)
from warera_mcp.auth.redaction import SecretRedactor, contains_control_characters
from warera_mcp.auth.requirements import (
    AuthRequirement,
    AuthResolution,
    CredentialKind,
    resolve_requirement,
)
from warera_mcp.auth.warnings import CLEARTEXT_CREDENTIAL_WARNING

__all__ = [
    "CLEARTEXT_CREDENTIAL_WARNING",
    "AuthRequirement",
    "AuthResolution",
    "CredentialError",
    "CredentialKind",
    "PlayerRequestContext",
    "SecretRedactor",
    "contains_control_characters",
    "parse_player_context",
    "resolve_requirement",
]
