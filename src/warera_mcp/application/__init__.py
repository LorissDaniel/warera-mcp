"""Semantic application services.

The MCP layer depends only on this package. Each service is constructed from a
:class:`~warera_mcp.application.common.ServiceRuntime` and contains no transport
or protocol details.
"""

from __future__ import annotations

from dataclasses import dataclass

from warera_mcp.application.articles import ArticleService
from warera_mcp.application.battles import BattleService
from warera_mcp.application.catalog import ItemCatalog
from warera_mcp.application.common import ServiceRuntime, UpstreamCaller
from warera_mcp.application.companies import CompanyService
from warera_mcp.application.events import EventService
from warera_mcp.application.game_configuration import GameConfigurationService
from warera_mcp.application.market import MarketService
from warera_mcp.application.players import PlayerResolver, PlayerService
from warera_mcp.application.rankings import BattleRankingService
from warera_mcp.application.world import WorldService


@dataclass(slots=True)
class Services:
    """The complete set of semantic services exposed through MCP tools."""

    players: PlayerService
    companies: CompanyService
    world: WorldService
    market: MarketService
    battles: BattleService
    events: EventService
    articles: ArticleService
    rankings: BattleRankingService
    catalog: ItemCatalog
    game_configuration: GameConfigurationService


def build_services(runtime: ServiceRuntime) -> Services:
    """Wire the service graph from a single runtime."""
    caller = UpstreamCaller(runtime)
    catalog = ItemCatalog(caller)
    game_configuration = GameConfigurationService(caller)
    resolver = PlayerResolver(caller, runtime)
    return Services(
        players=PlayerService(resolver),
        companies=CompanyService(caller, runtime, resolver, game_configuration),
        world=WorldService(caller, runtime),
        market=MarketService(caller, runtime, catalog),
        battles=BattleService(caller, runtime),
        events=EventService(caller, runtime),
        articles=ArticleService(caller),
        rankings=BattleRankingService(caller, runtime),
        catalog=catalog,
        game_configuration=game_configuration,
    )


__all__ = [
    "BattleRankingService",
    "BattleService",
    "CompanyService",
    "EventService",
    "ItemCatalog",
    "MarketService",
    "PlayerResolver",
    "PlayerService",
    "ServiceRuntime",
    "Services",
    "UpstreamCaller",
    "WorldService",
    "build_services",
]
