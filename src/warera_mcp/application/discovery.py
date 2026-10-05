"""Public identifier discovery, without automatic profile enrichment."""

from __future__ import annotations

from warera_mcp.application.common import ServiceRuntime, UpstreamCaller
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.extended_models import (
    CountryPlayerReference,
    CountryPlayersResult,
    EntitySearchResult,
)
from warera_mcp.domain.extended_normalization import object_rows, require_id, require_object
from warera_mcp.domain.normalization import page_info
from warera_mcp.warera.errors import WareraSchemaError
from warera_mcp.warera.schemas import as_sequence, extract_ident


class DiscoveryService:
    def __init__(self, caller: UpstreamCaller, runtime: ServiceRuntime) -> None:
        self._caller = caller
        self._runtime = runtime

    async def get_country_players(
        self,
        *,
        country_id: str,
        limit: int,
        cursor: str | None = None,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> CountryPlayersResult:
        params: dict[str, object] = {"countryId": country_id, "limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        read = await self._caller.read(
            "get_country_players",
            "user.getUsersByCountry",
            params,
            credentials=credentials,
            correlation_id=correlation_id,
        )
        r = require_object(read.data)
        rows = object_rows(r.raw("items"))
        truncated = len(rows) > limit
        return CountryPlayersResult(
            observed_at=read.observed_at,
            country_id=country_id,
            players=[
                CountryPlayerReference(
                    user_id=require_id(row), created_at=row.timestamp("createdAt")
                )
                for row in rows[:limit]
            ],
            page=page_info(read.data),
            partial=truncated,
            warnings=["upstream country-player page exceeded limit; rows omitted"]
            if truncated
            else [],
        )

    async def search_entities(
        self,
        *,
        search: str,
        offset: int,
        limit: int,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> EntitySearchResult:
        read = await self._caller.read(
            "search_entities",
            "search.searchAnything",
            {"searchText": search},
            credentials=credentials,
            correlation_id=correlation_id,
        )
        r = require_object(read.data)
        groups = {}
        counts = {}
        more = {}
        for key, name in (
            ("userIds", "user"),
            ("muIds", "military_unit"),
            ("countryIds", "country"),
            ("regionIds", "region"),
            ("partyIds", "party"),
            ("allianceIds", "alliance"),
        ):
            if not r.has(key):
                continue
            values = as_sequence(r.raw(key))
            if values is None:
                raise WareraSchemaError("invalid entity ID list")
            ids = [extract_ident(v) for v in values]
            if any(v is None for v in ids):
                raise WareraSchemaError("invalid entity ID")
            groups[name] = [v for v in ids[offset : offset + limit] if v is not None]
            counts[name] = len(ids)
            more[name] = offset + limit < len(ids)
        return EntitySearchResult(
            observed_at=read.observed_at,
            search=search,
            entity_ids=groups,
            snapshot_counts=counts,
            has_more=more,
            has_data=r.boolean("hasData"),
            offset=offset,
            limit=limit,
            warnings=["search matches are candidates, not exact identities or complete population"],
        )
