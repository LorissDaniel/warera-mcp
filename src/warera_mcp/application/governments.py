"""Additional cohesive read services, with reviewed scopes and bounded projections."""

from __future__ import annotations

import re

from warera_mcp.application.common import (
    ServiceRuntime,
    UpstreamCaller,
)
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    GovernmentResult,
)
from warera_mcp.domain.extended_normalization import (
    government_snapshot,
)
from warera_mcp.warera.client import UpstreamRead

CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class GovernmentService:
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

    async def get_government(
        self,
        *,
        country_id: str,
        offset: int,
        limit: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> GovernmentResult:
        read = await self._read(
            "get_country_government",
            "government.getByCountryId",
            {"countryId": country_id},
            credentials,
            correlation_id,
        )
        return government_snapshot(
            read.data,
            country_id=country_id,
            offset=offset,
            limit=limit,
            observed_at=read.observed_at,
        )
