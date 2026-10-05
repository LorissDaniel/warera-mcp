"""Semantic application services.

The MCP layer depends only on this package. Each service is constructed from a
:class:`~warera_mcp.application.common.ServiceRuntime` and contains no transport
or protocol details.
"""

from __future__ import annotations

from dataclasses import dataclass

from warera_mcp.application.articles import ArticleService
from warera_mcp.application.battle_details import BattleDetailService
from warera_mcp.application.battles import BattleService
from warera_mcp.application.catalog import ItemCatalog
from warera_mcp.application.common import ServiceRuntime, UpstreamCaller
from warera_mcp.application.companies import CompanyService
from warera_mcp.application.discovery import DiscoveryService
from warera_mcp.application.equipment import EquipmentService
from warera_mcp.application.events import EventService
from warera_mcp.application.game_configuration import GameConfigurationService
from warera_mcp.application.global_rankings import GlobalRankingService
from warera_mcp.application.governments import GovernmentService
from warera_mcp.application.locations import LocationService
from warera_mcp.application.market import MarketService
from warera_mcp.application.mercenary_auctions import MercenaryAuctionService
from warera_mcp.application.military_units import MilitaryUnitService
from warera_mcp.application.players import PlayerResolver, PlayerService
from warera_mcp.application.rankings import BattleRankingService
from warera_mcp.application.resources import ResourceService
from warera_mcp.application.transactions import TransactionService
from warera_mcp.application.upgrades import UpgradeService
from warera_mcp.application.workforce import WorkforceService
from warera_mcp.application.world import WorldService


@dataclass(slots=True)
class Services:
    """The complete set of semantic services exposed through MCP tools."""

    discovery: DiscoveryService
    upgrades: UpgradeService
    equipment: EquipmentService
    battle_details: BattleDetailService
    mercenary_auctions: MercenaryAuctionService
    governments: GovernmentService
    global_rankings: GlobalRankingService
    workforce: WorkforceService
    transactions: TransactionService
    military_units: MilitaryUnitService
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
    locations: LocationService
    resources: ResourceService


def build_services(runtime: ServiceRuntime) -> Services:
    """Wire the service graph from a single runtime."""
    caller = UpstreamCaller(runtime)
    catalog = ItemCatalog(caller)
    game_configuration = GameConfigurationService(caller)
    resolver = PlayerResolver(caller, runtime)
    return Services(
        discovery=DiscoveryService(caller, runtime),
        transactions=TransactionService(caller, runtime),
        workforce=WorkforceService(caller, runtime),
        global_rankings=GlobalRankingService(caller, runtime),
        governments=GovernmentService(caller, runtime),
        mercenary_auctions=MercenaryAuctionService(caller, runtime),
        battle_details=BattleDetailService(caller, runtime),
        equipment=EquipmentService(caller, runtime, resolver),
        upgrades=UpgradeService(caller, runtime),
        military_units=MilitaryUnitService(caller, runtime),
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
        locations=LocationService(caller),
        resources=ResourceService(caller, resolver),
    )


__all__ = [
    "BattleRankingService",
    "BattleService",
    "CompanyService",
    "EventService",
    "ItemCatalog",
    "LocationService",
    "MarketService",
    "MilitaryUnitService",
    "PlayerResolver",
    "PlayerService",
    "ResourceService",
    "ServiceRuntime",
    "Services",
    "UpstreamCaller",
    "WorldService",
    "build_services",
]
