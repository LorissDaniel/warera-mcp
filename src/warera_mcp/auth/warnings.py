"""Credential-input disclosures required by the security model.

The v1 design deliberately accepts WarEra credentials as ordinary tool request
fields and relies on an explicit cleartext warning rather than a specialised
secure-input channel. These constants are the single source of that disclosure:
server instructions, tool descriptions, and missing-auth guidance all reuse
them so the wording can never drift out of sync.
"""

from __future__ import annotations

CLEARTEXT_CREDENTIAL_WARNING = (
    "Security notice: WarEra credentials supplied to this MCP tool are processed "
    "in clear within the MCP request and may be visible in the LLM/MCP client's "
    "conversation or tool-call history. Provide them only if you accept this "
    "handling. The server never logs, echoes, or stores them, and it will not use "
    "a credential for an operation that was not verified to accept it."
)

CREDENTIAL_ASK_INSTRUCTION = (
    "Do not ask for credentials pre-emptively. Call the requested tool first; if it "
    "returns a missing-authentication error, explain which credential kind is required. "
    "Before asking the user for a WarEra API key or JWT, show them the cleartext handling "
    "warning. Credentials are identifiers' secrets, not player identity: a user ID or "
    "username never authenticates a caller."
)

__all__ = ["CLEARTEXT_CREDENTIAL_WARNING", "CREDENTIAL_ASK_INSTRUCTION"]
