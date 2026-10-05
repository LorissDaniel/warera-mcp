"""Additional cohesive read services, with reviewed scopes and bounded projections."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from warera_mcp import errors
from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    bounded_try_reads,
    composite_observed_at,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    EntityUpgradesResult,
)
from warera_mcp.domain.extended_normalization import (
    entity_upgrade,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.errors import WareraSchemaError

COMPANY_UPGRADES = ("storage", "automatedEngine", "breakRoom")
REGION_UPGRADES = ("bunker", "base", "pacificationCenter")


class UpgradeService:
    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    async def _read(
        self,
        operation: str,
        procedure: str,
        params: dict[str, object],
        credentials: PlayerRequestContext | None,
        correlation_id: str | None,
    ) -> UpstreamRead:
        return await self._caller.read(
            operation, procedure, params, credentials=credentials, correlation_id=correlation_id
        )

    async def get_upgrades(
        self,
        *,
        entity_type: Literal["company", "region"],
        entity_id: str,
        upgrade_types: Sequence[str] | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> EntityUpgradesResult:
        operation = f"get_{entity_type}_upgrades"
        allowed = COMPANY_UPGRADES if entity_type == "company" else REGION_UPGRADES
        kinds = list(dict.fromkeys(upgrade_types)) if upgrade_types else list(allowed)
        if any(k not in allowed for k in kinds):
            raise errors.invalid_input("upgrade type does not match entity", operation)
        outcomes = await bounded_try_reads(
            (
                self._caller.try_read(
                    operation,
                    "upgrade.getUpgradeByTypeAndEntity",
                    {f"{entity_type}Id": entity_id, "upgradeType": k},
                    credentials=credentials,
                    correlation_id=correlation_id,
                )
                for k in kinds
            ),
            limit=self._runtime.max_fanout,
        )
        upgrades = []
        absent = []
        unavailable = []
        reads = []
        warnings = []
        for kind, out in zip(kinds, outcomes, strict=True):
            if isinstance(out, UpstreamRead):
                reads.append(out)
                if out.data is None:
                    absent.append(kind)
                    continue
                try:
                    upgrades.append(entity_upgrade(out.data, entity_type, entity_id, kind))
                    continue
                except WareraSchemaError:
                    pass
            unavailable.append(kind)
            warnings.append(f"{kind} upgrade was unavailable or invalid")
        return EntityUpgradesResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            entity_type=entity_type,
            entity_id=entity_id,
            upgrades=upgrades,
            absent_upgrade_types=absent,
            unavailable_upgrade_types=unavailable,
            partial=bool(unavailable),
        )
