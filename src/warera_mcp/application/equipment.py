"""Additional cohesive read services, with reviewed scopes and bounded projections."""

from __future__ import annotations

import re

from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
    composite_observed_at,
)
from warera_mcp.application.players import PlayerResolver
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    EquipmentResult,
)
from warera_mcp.domain.extended_normalization import (
    equipment_item,
    require_object,
)
from warera_mcp.warera.client import UpstreamRead

CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class EquipmentService:
    def __init__(
        self, caller: UpstreamCaller, runtime: ServiceRuntime, players: PlayerResolver
    ) -> None:
        self._caller = caller
        self._runtime = runtime
        self._players = players

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

    async def get_equipment(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> EquipmentResult:
        resolved = await self._players.resolve(
            user_id=user_id,
            username=username,
            credentials=credentials,
            correlation_id=correlation_id,
            operation="get_player_equipment",
        )
        read = await self._read(
            "get_player_equipment",
            "inventory.fetchCurrentEquipment",
            {"userId": resolved.profile.id},
            credentials,
            correlation_id,
        )
        r = require_object(read.data)
        items = {}
        empty = []
        warnings = list(resolved.warnings)
        skills_truncated = False
        for slot, value in list(r.data.items())[:20]:
            if not CODE.fullmatch(slot):
                continue
            if value is None:
                empty.append(slot)
            else:
                items[slot] = equipment_item(value)
                skills = require_object(value).raw("skills")
                if isinstance(skills, dict) and len(skills) > 64:
                    warnings.append(f"{slot} skill map truncated to 64 entries")
                    skills_truncated = True
        truncated = len(r.data) > 20
        if truncated:
            warnings.append("equipped slots truncated to 20")
        return EquipmentResult(
            observed_at=composite_observed_at([*resolved.reads, read]),
            warnings=warnings,
            user_id=resolved.profile.id,
            equipment=items,
            empty_slots=empty,
            partial=truncated or skills_truncated,
        )
