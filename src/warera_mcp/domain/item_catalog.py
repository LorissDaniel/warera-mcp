"""Versioned WarEra item-code catalog exposed to the LLM."""

from __future__ import annotations

from typing import Final

# Keep this list canonical: no translations or aliases belong in the MCP.
# Add newly introduced WarEra item codes here after verification.
WARERA_ITEM_CODES: Final[tuple[str, ...]] = (
    "ammo",
    "bread",
    "case1",
    "case2",
    "coca",
    "cocain",
    "concrete",
    "cookedFish",
    "fish",
    "grain",
    "heavyAmmo",
    "iron",
    "lead",
    "limestone",
    "lightAmmo",
    "livestock",
    "oil",
    "paper",
    "petroleum",
    "scraps",
    "steak",
    "steel",
    "wood",
    "woodenCase",
)

__all__ = ["WARERA_ITEM_CODES"]
