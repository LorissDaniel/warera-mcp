"""Local production-recipe catalog seam.

Recipes are *not* available from any verified WarEra read operation. Rather than
inventing recipe facts, the project exposes a small catalog interface with a
null default: ``get_company_overview`` simply omits the recipe section unless a
validated catalog is supplied. This keeps the extension point without shipping
unverified game data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from warera_mcp.domain.models import Recipe, RecipeInput


@runtime_checkable
class RecipeCatalog(Protocol):
    """Looks up locally maintained recipe facts by produced item code."""

    @property
    def version(self) -> str | None: ...

    def lookup(self, item_code: str) -> Recipe | None: ...


class NullRecipeCatalog:
    """Default catalog: no recipe data is asserted."""

    @property
    def version(self) -> str | None:
        return None

    def lookup(self, item_code: str) -> Recipe | None:
        return None


@dataclass(frozen=True, slots=True)
class StaticRecipeCatalog:
    """A versioned, explicitly sourced catalog of recipes.

    ``entries`` maps a produced item code to ``(pp_per_unit, inputs)`` where
    ``inputs`` is a sequence of ``(item_code, quantity)`` pairs.
    """

    entries: Mapping[str, tuple[float | None, Sequence[tuple[str, float | None]]]]
    version: str
    source: str = "local_catalog"
    _cache: dict[str, Recipe] = field(default_factory=dict, init=False, repr=False, compare=False)

    def lookup(self, item_code: str) -> Recipe | None:
        cached = self._cache.get(item_code)
        if cached is not None:
            return cached
        entry = self.entries.get(item_code)
        if entry is None:
            return None
        pp_per_unit, inputs = entry
        recipe = Recipe(
            pp_per_unit=pp_per_unit,
            inputs=[RecipeInput(item_code=code, quantity=qty) for code, qty in inputs],
            source=self.source,
            version=self.version,
        )
        self._cache[item_code] = recipe
        return recipe

    def lookup_many(self, item_codes: Sequence[str]) -> dict[str, Recipe]:
        found: dict[str, Recipe] = {}
        for code in item_codes:
            recipe = self.lookup(code)
            if recipe is not None:
                found[code] = recipe
        return found


__all__ = ["NullRecipeCatalog", "RecipeCatalog", "StaticRecipeCatalog"]
