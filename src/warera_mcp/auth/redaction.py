"""Pure helpers that keep credential material out of logs, errors and caches.

Nothing here ever mutates or inspects credential semantics beyond redacting it.
The helpers are intentionally dependency-free so they can be reused by logging,
error rendering, telemetry and tests.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "[redacted]"

#: Control characters are rejected in credential/identifier inputs because they
#: enable header injection and log spoofing. All C0 controls (including CR/LF/TAB)
#: and DEL are rejected.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def contains_control_characters(value: str) -> bool:
    """Return ``True`` when *value* contains ASCII control characters."""
    return bool(_CONTROL_CHARS.search(value))


class SecretRedactor:
    """Scrubs a request's credential values from any outgoing text.

    A redactor is built once per tool invocation from the submitted credential
    values and handed to the error/log boundaries. Empty and very short values
    are ignored so the redactor never masks ordinary prose.
    """

    __slots__ = ("_secrets",)

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        self._secrets = tuple(
            sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)
        )

    @property
    def has_secrets(self) -> bool:
        return bool(self._secrets)

    def scrub(self, text: str) -> str:
        """Replace every known secret occurrence with :data:`REDACTED`."""
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def scrub_object(self, value: object) -> object:
        """Recursively scrub strings inside a JSON-like object."""
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {self.scrub(str(k)): self.scrub_object(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub_object(item) for item in value]
        return value


__all__ = ["REDACTED", "SecretRedactor", "contains_control_characters"]
