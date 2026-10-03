"""Canonical domain layer: typed models, enums, normalization and catalog seams."""

from __future__ import annotations

from warera_mcp.domain.enums import (
    BattleEntityType,
    BattleMetric,
    BattleSide,
    PlayerField,
    canonical_enum,
)
from warera_mcp.domain.recipes import NullRecipeCatalog, RecipeCatalog, StaticRecipeCatalog

__all__ = [
    "BattleEntityType",
    "BattleMetric",
    "BattleSide",
    "NullRecipeCatalog",
    "PlayerField",
    "RecipeCatalog",
    "StaticRecipeCatalog",
    "canonical_enum",
]
