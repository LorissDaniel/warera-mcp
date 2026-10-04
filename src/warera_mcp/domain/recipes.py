"""Explicit production-recipe catalog overrides.

Company overview checks this synchronous catalog first, then can fall back to
the asynchronous official configuration service. A null catalog therefore
does not suppress recipes available in WarEra's official configuration. Static
catalogs remain useful for tests and explicit caller-provided overrides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from warera_mcp.domain.models import Recipe, RecipeInput


@runtime_checkable
class RecipeCatalog(Protocol):
    """Looks up explicitly supplied recipe facts by produced item code."""

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
