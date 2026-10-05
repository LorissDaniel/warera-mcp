"""Endpoint-level capability requirements and credential resolution.

The authentication matrix is intentionally explicit and *per operation*: the
project never infers that one credential substitutes for another, and it never
ranks credentials by "strength". An operation is satisfied by exactly the
credential kinds that were verified for it.

For ``ANY_OF`` requirements a deterministic selection order is applied, preferring
API key where an operation explicitly permits both. Current operations declare a
single credential kind or public access; region recommendations permit API key only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self


class CredentialKind(StrEnum):
    """A class of WarEra credential. Never holds a value."""

    API_KEY = "API_KEY"
    JWT = "JWT"


#: Selection order for ``ANY_OF`` operations. Only kinds present in the
#: requirement can be selected; the tail is a deterministic fallback for
#: future credential kinds.
_SELECTION_PRIORITY: tuple[CredentialKind, ...] = (CredentialKind.API_KEY, CredentialKind.JWT)


@dataclass(frozen=True, slots=True)
class AuthRequirement:
    """Declarative credential requirement of one upstream operation."""

    mode: Literal["PUBLIC", "SINGLE", "ANY_OF"]
    kinds: tuple[CredentialKind, ...] = ()

    @classmethod
    def public(cls) -> Self:
        return cls(mode="PUBLIC")

    @classmethod
    def of(cls, kind: CredentialKind) -> Self:
        return cls(mode="SINGLE", kinds=(kind,))

    @classmethod
    def any_of(cls, *kinds: CredentialKind) -> Self:
        if not kinds:
            raise ValueError("ANY_OF requires at least one credential kind")
        return cls(mode="ANY_OF", kinds=tuple(dict.fromkeys(kinds)))

    @property
    def is_public(self) -> bool:
        return self.mode == "PUBLIC"

    @property
    def required_kinds(self) -> tuple[CredentialKind, ...]:
        """The full set of credential kinds that would satisfy the operation."""
        return self.kinds

    def satisfied_by(self, available: frozenset[CredentialKind]) -> bool:
        if self.is_public:
            return True
        return any(kind in available for kind in self.kinds)

    def select(self, available: frozenset[CredentialKind]) -> CredentialKind | None:
        """Choose exactly one credential kind to send, or ``None`` if public."""
        if self.is_public:
            return None
        for kind in _SELECTION_PRIORITY:
            if kind in self.kinds and kind in available:
                return kind
        for kind in self.kinds:  # pragma: no cover - future kinds only
            if kind in available:
                return kind
        return None

    def missing_from(self, available: frozenset[CredentialKind]) -> tuple[CredentialKind, ...]:
        if self.satisfied_by(available):
            return ()
        return tuple(kind for kind in self.kinds if kind not in available)

    def describe(self) -> str:
        """Human-readable, value-free description for errors and docs."""
        if self.is_public:
            return "PUBLIC"
        if self.mode == "SINGLE":
            return self.kinds[0].value
        return " or ".join(kind.value for kind in self.kinds)


@dataclass(frozen=True, slots=True)
class AuthResolution:
    """Outcome of resolving one requirement against available credentials."""

    requirement: AuthRequirement
    available: frozenset[CredentialKind]
    selected: CredentialKind | None
    missing: tuple[CredentialKind, ...]

    @property
    def satisfied(self) -> bool:
        return not self.missing

    @property
    def required(self) -> tuple[CredentialKind, ...]:
        return self.requirement.required_kinds


def resolve_requirement(
    requirement: AuthRequirement,
    available: frozenset[CredentialKind],
) -> AuthResolution:
    """Resolve a requirement without ever touching credential values."""
    return AuthResolution(
        requirement=requirement,
        available=available,
        selected=requirement.select(available),
        missing=requirement.missing_from(available),
    )


__all__ = [
    "AuthRequirement",
    "AuthResolution",
    "CredentialKind",
    "resolve_requirement",
]
