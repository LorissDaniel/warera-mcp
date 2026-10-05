"""Canonical, unknown-tolerant enumerations.

WarEra is an unofficial, evolving API. New enum members must never crash a tool,
become executable instructions, or be silently dropped. :func:`canonical_enum`
validates known members and maps anything else to a sanitized ``unknown:<value>``
token so callers can still observe drift without trusting the raw string.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from enum import StrEnum

from warera_mcp.warera.schemas import coerce_str

_SAFE_UNKNOWN = re.compile(r"[^a-z0-9_.:+-]")


class BattleEntityType(StrEnum):
    """Entity dimension of a battle ranking."""

    USER = "user"
    COUNTRY = "country"
    MU = "mu"


class BattleSide(StrEnum):
    """Side dimension of a battle ranking."""

    ATTACKER = "attacker"
    DEFENDER = "defender"
    MERGED = "merged"


class BattleMetric(StrEnum):
    """Metric dimension of a battle ranking."""

    DAMAGE = "damage"
    POINTS = "points"
    MONEY = "money"


class PlayerField(StrEnum):
    """Projection selector for :func:`~warera_mcp.application.players.get_player`."""

    PROFILE = "profile"
    LOCATION = "location"
    LEVEL = "level"
    SKILLS_SUMMARY = "skills_summary"
    RANKINGS_SUMMARY = "rankings_summary"
    ACTIVITY = "activity"
    STATISTICS = "statistics"
    MISSIONS = "missions"
    EQUIPMENT = "equipment"


def canonical_enum(value: object, allowed: Collection[str]) -> str | None:
    """Return a known enum member or a sanitized ``unknown:<value>`` token."""
    text = coerce_str(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in allowed:
        return lowered
    safe = _SAFE_UNKNOWN.sub("", lowered)[:48]
    return f"unknown:{safe}" if safe else "unknown"


__all__ = [
    "BattleEntityType",
    "BattleMetric",
    "BattleSide",
    "PlayerField",
    "canonical_enum",
]


# Documented event filter codes; casing is significant upstream.
EVENT_TYPES: tuple[str, ...] = (
    "warDeclared",
    "peace_agreement",
    "battleOpened",
    "battleEnded",
    "newPresident",
    "regionTransfer",
    "peaceMade",
    "countryMoneyTransfer",
    "depositDiscovered",
    "depositDepleted",
    "systemRevolt",
    "bankruptcy",
    "allianceFormed",
    "allianceBroken",
    "allianceMemberJoined",
    "allianceMemberLeft",
    "allianceMemberExcluded",
    "defensivePactFormed",
    "defensivePactBroken",
    "regionLiberated",
    "strategicResourcesReshuffled",
    "resistanceIncreased",
    "resistanceDecreased",
    "revolutionStarted",
    "revolutionEnded",
    "financedRevolt",
)
