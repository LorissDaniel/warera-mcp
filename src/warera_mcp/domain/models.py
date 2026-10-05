"""Canonical domain models — the stable, LLM-facing output shapes.

These models are deliberately independent of upstream wire spelling: field names
are snake_case, identifiers are opaque strings, timestamps are RFC 3339 UTC with
a ``Z`` suffix, and percentages are normalised to fractions for arithmetic.

Two naming rules are applied consistently across every tool:

* ``warnings`` is the single diagnostics list (the design sketch sometimes calls
  it ``limitations``; one consistent name is required by the token-optimisation
  rules).
* ``None`` means "not observed / not verified". Absent numeric facts are never
  replaced with ``0``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def to_rfc3339_z(value: datetime) -> str:
    """Serialize an aware datetime as RFC 3339 UTC with a ``Z`` suffix."""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


#: Timestamp type used by every canonical model.
UtcDateTime = Annotated[datetime, PlainSerializer(to_rfc3339_z, return_type=str)]


class DomainModel(BaseModel):
    """Base for canonical models: strict fields, no silent extras."""

    model_config = ConfigDict(extra="forbid")


class ToolResult(DomainModel):
    """Base envelope shared by every tool output."""

    observed_at: UtcDateTime
    warnings: list[str] = Field(default_factory=list)

    def to_structured(self) -> dict[str, Any]:
        """Compact structured content with a consistent null-omission policy."""
        return self.model_dump(mode="json", exclude_none=True)


class ConfigurationProvenance(DomainModel):
    source: str = "official_game_configuration"
    source_procedure: str
    source_url: str
    configuration_version: str
    observed_at: UtcDateTime
    freshness_seconds: int


class GameRulesResult(ToolResult):
    provenance: ConfigurationProvenance
    topic: str
    records: list[dict[str, Any]]
    offset: int
    limit: int
    total_count: int
    has_more: bool
    partial: bool = False


class SkillProgressionResult(ToolResult):
    provenance: ConfigurationProvenance
    skill_code: str
    levels: list[dict[str, Any]]
    offset: int
    limit: int
    total_count: int
    has_more: bool
    partial: bool = False


class ItemDetailsResult(ToolResult):
    provenance: ConfigurationProvenance
    item: dict[str, Any]
    partial: bool = False


class GameScheduleResult(ToolResult):
    provenance: ConfigurationProvenance
    schedule: dict[str, UtcDateTime]
    partial: bool = False


class PageInfo(DomainModel):
    """Opaque upstream cursor plus a best-effort continuation hint."""

    next_cursor: str | None = None
    has_more: bool = False


# --------------------------------------------------------------------------- players
class PlayerRef(DomainModel):
    """Minimal, stable player identity used inside composite results."""

    id: str
    username: str | None = None


class PlayerProfile(DomainModel):
    """Public player profile projection."""

    id: str
    username: str | None = None
    level: int | None = None
    country_id: str | None = None
    region_id: str | None = None
    skills: dict[str, float] | None = None
    rankings: dict[str, float] | None = None


class GetPlayerResult(ToolResult):
    """Output of ``get_player``."""

    player: PlayerProfile
    resolved_by: Literal["user_id", "username"]


class PlayerResourcesResult(ToolResult):
    """Available resources and market reservations, never inferred from wealth stats."""

    player: PlayerRef
    inventory_id: str
    money_available: float | None = None
    items_available: dict[str, float] | None = None
    money_reserved: float | None = None
    items_reserved_for_market: dict[str, float] | None = None
    sell_quantities: dict[str, float] | None = None
    money_in_buy_orders: float | None = None
    expected_sales_proceeds: float | None = None
    orders_observed_at: UtcDateTime | None = None
    orders_requested: bool
    partial: bool = False


class RecommendedRegion(DomainModel):
    region_id: str
    region_name: str | None = None
    country_id: str | None = None
    country_name: str | None = None
    production_bonus: ProductionBonus
    tax_fraction: float | None = None


class RecommendedRegionsResult(ToolResult):
    item_code: str
    include_deposit: bool
    regions: list[RecommendedRegion]
    total_count: int
    has_more: bool
    source: Literal["game_recommendations"] = "game_recommendations"
    partial: bool = False


# ------------------------------------------------------------------------- companies
class CompanySummary(DomainModel):
    """One owned company with optional production bonus enrichment."""

    id: str
    name: str | None = None
    item_code: str | None = None
    region_id: str | None = None
    worker_count: int | None = None
    production_stored: float | None = None
    upgrade_levels: dict[str, float] | None = None
    production_bonus: float | None = None


class GetPlayerCompaniesResult(ToolResult):
    """Output of ``get_player_companies``."""

    player: PlayerRef
    companies: list[CompanySummary]
    total_count: int
    has_more: bool
    partial: bool = False


class ProductionBonus(DomainModel):
    """Production bonus components normalised to fractions of 1.0."""

    total_fraction: float | None = None
    components: dict[str, float] = Field(default_factory=dict)


class RecipeInput(DomainModel):
    """One required input of a production recipe."""

    item_code: str
    quantity: float | None = None


class Recipe(DomainModel):
    """Recipe facts from an explicit catalog or official configuration snapshot."""

    pp_per_unit: float | None = None
    inputs: list[RecipeInput] = Field(default_factory=list)
    source: str = "local_catalog"
    version: str | None = None
    observed_at: UtcDateTime | None = None
    source_procedure: str | None = None


class CompanyDetail(DomainModel):
    """Canonical company facts."""

    id: str
    name: str | None = None
    owner_id: str | None = None
    region_id: str | None = None
    item_code: str | None = None
    stored_production: float | None = None
    storage_level: int | None = None
    automated_engine_level: int | None = None
    worker_count: int | None = None
    created_at: UtcDateTime | None = None


class GetCompanyOverviewResult(ToolResult):
    """Output of ``get_company_overview``."""

    company: CompanyDetail
    production_bonus: ProductionBonus | None = None
    recipe: Recipe | None = None
    region: RegionSummary | None = None
    partial: bool = False


# ----------------------------------------------------------------------------- world
class CountryFacts(DomainModel):
    """Selected, verified country facts only."""

    id: str
    name: str | None = None
    code: str | None = None
    population: int | None = None
    development: float | None = None
    taxes: dict[str, float] | None = None
    specialized_item: str | None = None
    wars_with: list[str] | None = None
    allies: list[str] | None = None
    alliance_id: str | None = None
    enemy: str | None = None


class RegionSummary(DomainModel):
    """Compact region facts used inside country overviews."""

    id: str
    name: str | None = None
    population: int | None = None
    development: float | None = None
    climate: str | None = None
    biome: str | None = None


class GetCountryOverviewResult(ToolResult):
    """Output of ``get_country_overview``."""

    country: CountryFacts
    regions: list[RegionSummary] | None = None
    regions_page: PageInfo | None = None


class CountryRef(DomainModel):
    """Minimal country identity."""

    id: str
    name: str | None = None
    code: str | None = None


class Deposit(DomainModel):
    """Region deposit window; bonus is normalised to a fraction."""

    type: str | None = None
    bonus_fraction: float | None = None
    starts_at: UtcDateTime | None = None
    ends_at: UtcDateTime | None = None


class RegionDetail(DomainModel):
    """Region detail projection."""

    id: str
    name: str | None = None
    country_id: str | None = None
    is_capital: bool | None = None
    climate: str | None = None
    biome: str | None = None
    population: int | None = None
    development: float | None = None
    deposit: Deposit | None = None
    neighbors: list[str] | None = None
    upgrades: dict[str, float] | None = None


class GetRegionResult(ToolResult):
    """Output of ``get_region``."""

    region: RegionDetail
    country: CountryRef | None = None


class OpponentCountry(DomainModel):
    """One current war counterparty; name may be unresolved."""

    country_id: str
    name: str | None = None


class BattleSideRef(DomainModel):
    """One side of a battle, compacted for list views."""

    country_id: str | None = None
    country_name: str | None = None
    region_id: str | None = None
    damages: float | None = None
    won_rounds: int | None = None


class BattleSummary(DomainModel):
    """Compact battle row for list views."""

    id: str
    type: str | None = None
    is_active: bool | None = None
    attacker: BattleSideRef | None = None
    defender: BattleSideRef | None = None
    current_round: str | None = None
    created_at: UtcDateTime | None = None


class GetCountryWarsResult(ToolResult):
    """Output of ``get_country_wars``."""

    country: CountryRef
    opponents: list[OpponentCountry]
    active_battles: list[BattleSummary] | None = None
    page: PageInfo
    partial: bool = False


# ---------------------------------------------------------------------------- market
class MarketPrice(ToolResult):
    """Output of ``get_market_price``."""

    item_code: str
    price: float
    source: Literal["global_price"] = "global_price"
    freshness_seconds: float | None = None


class ItemCatalogResult(ToolResult):
    """Output of the local WarEra item-code catalog."""

    item_codes: list[str] = Field(default_factory=list)
    source: Literal["local_verified_catalog"] = "local_verified_catalog"
    catalog_version: str


class MarketPricesResult(ToolResult):
    """Output of ``get_market_prices``."""

    prices: dict[str, float] = Field(default_factory=dict)
    source: Literal["global_prices"] = "global_prices"
    freshness_seconds: float | None = None


class OrderBookLevel(DomainModel):
    """One visible price level; order owners are intentionally omitted."""

    price: float
    quantity: float | None = None
    offer_at: UtcDateTime | None = None


class OrderBookDepth(DomainModel):
    """Aggregate visible depth for one side of the book."""

    quantity: float
    estimated_vwap: float | None = None


class OrderBookResult(ToolResult):
    """Output of ``search_market``."""

    item_code: str
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    buy_orders: list[OrderBookLevel] = Field(default_factory=list)
    sell_orders: list[OrderBookLevel] = Field(default_factory=list)
    depth: OrderBookDepth | None = None


# ------------------------------------------------------------------------------ work
class WageStats(DomainModel):
    """Wage benchmark for one item; gross/net semantics untouched."""

    min: float | None = None
    max: float | None = None
    average: float | None = None
    top_offer: float | None = None
    top_eligible_offer: float | None = None
    top_eligible_offers: list[float] | None = None


class WorkOffer(DomainModel):
    """One work offer, projected and optionally locally filtered."""

    company_id: str | None = None
    region_id: str | None = None
    quantity: float | None = None
    wage: float | None = None
    wage_after_tax: float | None = None
    citizenship: str | None = None
    text: str | None = None


class GetWorkMarketResult(ToolResult):
    """Output of ``get_work_market``."""

    item_code: str
    wage_stats: WageStats | None = None
    offers: list[WorkOffer] = Field(default_factory=list)
    partial: bool = False


# --------------------------------------------------------------------------- battles
class RoundSummary(DomainModel):
    """Current-round numeric summary."""

    round_id: str | None = None
    attacker_damages: float | None = None
    defender_damages: float | None = None
    attacker_points: float | None = None
    defender_points: float | None = None
    next_tick_at: UtcDateTime | None = None


class BattleDetail(DomainModel):
    """Battle dossier without last-hits/equipment payloads."""

    id: str
    type: str | None = None
    is_active: bool | None = None
    attacker: BattleSideRef | None = None
    defender: BattleSideRef | None = None
    current_round: RoundSummary | None = None
    rounds_to_win: int | None = None
    created_at: UtcDateTime | None = None
    round_history: list[RoundSummary] | None = None


class BattleLiveStatus(DomainModel):
    """Volatile live snapshot, always timestamped."""

    round_id: str | None = None
    attacker_damages: float | None = None
    defender_damages: float | None = None
    attacker_points: float | None = None
    defender_points: float | None = None
    next_tick_at: UtcDateTime | None = None


class SearchBattlesResult(ToolResult):
    """Output of ``search_battles``."""

    battles: list[BattleSummary]
    page: PageInfo


class GetBattleResult(ToolResult):
    """Output of ``get_battle``."""

    battle: BattleDetail
    live_status: BattleLiveStatus | None = None
    partial: bool = False


class BattleRankingEntry(DomainModel):
    """One ranked entity in a battle leaderboard."""

    rank: int | None = None
    entity_id: str | None = None
    name: str | None = None
    value: float | None = None


class BattleRankingResult(ToolResult):
    """Output of ``get_battle_ranking``."""

    battle_id: str
    entity_type: str
    side: str
    metric: str
    item_count: int
    entries: list[BattleRankingEntry]
    truncated: bool = False


# ---------------------------------------------------------------------------- events
class EventSummary(DomainModel):
    """One world event; ``summary`` is sanitized untrusted text."""

    id: str
    type: str | None = None
    occurred_at: UtcDateTime | None = None
    related_ids: list[str] = Field(default_factory=list)
    summary: str | None = None


class ArticleStats(DomainModel):
    likes: int | None = None
    dislikes: int | None = None
    score: int | None = None
    views: int | None = None
    comments: int | None = None


class Article(DomainModel):
    """Published article metadata and optionally sanitized, untrusted text."""

    id: str
    title: str | None = None
    author_id: str | None = None
    language: str | None = None
    category: str | None = None
    published_at: UtcDateTime | None = None
    stats: ArticleStats | None = None
    content: str | None = None
    content_truncated: bool = False


class SearchArticlesResult(ToolResult):
    articles: list[Article]
    page: PageInfo
    feed: str
    content_included: bool
    source_procedure: str = "article.getArticlesPaginated"


class GetArticleResult(ToolResult):
    article: Article
    source_procedure: str = "article.getArticleLiteById"


class SearchEventsResult(ToolResult):
    """Output of ``search_events``."""

    events: list[EventSummary]
    page: PageInfo


__all__ = [
    "Article",
    "ArticleStats",
    "BattleDetail",
    "BattleLiveStatus",
    "BattleRankingEntry",
    "BattleRankingResult",
    "BattleSideRef",
    "BattleSummary",
    "CompanyDetail",
    "CompanySummary",
    "CountryFacts",
    "CountryRef",
    "Deposit",
    "DomainModel",
    "EventSummary",
    "GetArticleResult",
    "GetBattleResult",
    "GetCompanyOverviewResult",
    "GetCountryOverviewResult",
    "GetCountryWarsResult",
    "GetPlayerCompaniesResult",
    "GetPlayerResult",
    "GetRegionResult",
    "GetWorkMarketResult",
    "MarketPrice",
    "OpponentCountry",
    "OrderBookDepth",
    "OrderBookLevel",
    "OrderBookResult",
    "PageInfo",
    "PlayerProfile",
    "PlayerRef",
    "PlayerResourcesResult",
    "ProductionBonus",
    "Recipe",
    "RecipeInput",
    "RecommendedRegion",
    "RecommendedRegionsResult",
    "RegionDetail",
    "RegionSummary",
    "RoundSummary",
    "SearchArticlesResult",
    "SearchBattlesResult",
    "SearchEventsResult",
    "ToolResult",
    "UtcDateTime",
    "WageStats",
    "WorkOffer",
    "to_rfc3339_z",
]
