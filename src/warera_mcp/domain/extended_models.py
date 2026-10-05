"""Typed, bounded projections for additional read-only game capabilities."""

from __future__ import annotations

from pydantic import Field

from warera_mcp.domain.models import (
    DomainModel,
    PageInfo,
    RoundSummary,
    ToolResult,
    UtcDateTime,
    WorkOffer,
)


class EquipmentItem(DomainModel):
    id: str | None = None
    code: str | None = None
    type: str | None = None
    skills: dict[str, float] | None = None
    state: float | None = None
    max_state: float | None = None
    quantity: float | None = None
    last_acquisition_at: UtcDateTime | None = None


class EquipmentResult(ToolResult):
    user_id: str
    equipment: dict[str, EquipmentItem]
    empty_slots: list[str] = Field(default_factory=list)
    partial: bool = False


class EntityUpgrade(DomainModel):
    id: str
    company_id: str | None = None
    region_id: str | None = None
    upgrade_type: str
    level: int | None = None
    status: str | None = None
    invested_money: float | None = None
    invested_concrete: float | None = None
    invested_steel: float | None = None
    dependant_users_count: int | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    status_changed_at: UtcDateTime | None = None
    will_be_active_at: UtcDateTime | None = None
    last_upgrade_at: UtcDateTime | None = None
    last_downgrade_at: UtcDateTime | None = None


class EntityUpgradesResult(ToolResult):
    entity_type: str
    entity_id: str
    upgrades: list[EntityUpgrade]
    absent_upgrade_types: list[str] = Field(default_factory=list)
    unavailable_upgrade_types: list[str] = Field(default_factory=list)
    partial: bool = False


class RoundResult(ToolResult):
    round: RoundSummary


class BattleHit(DomainModel):
    id: str | None = None
    user_id: str | None = None
    military_unit_id: str | None = None
    damages: float | None = None
    is_critical_hit: bool | None = None
    is_missed: bool | None = None
    hit_at: UtcDateTime | None = None
    weapon: EquipmentItem | None = None
    equipment: list[EquipmentItem] | None = None
    ammo: str | None = None
    equipment_truncated: bool = False


class RoundHitsResult(ToolResult):
    round_id: str
    hits: dict[str, list[BattleHit]]
    snapshot_counts: dict[str, int]
    has_more: dict[str, bool]
    offset: int
    limit: int
    equipment_included: bool
    partial: bool = False


class BattleOrder(DomainModel):
    id: str
    battle_id: str
    side: str
    country_id: str | None = None
    military_unit_id: str | None = None
    user_id: str | None = None
    side_country_id: str | None = None
    priority: str | None = None
    is_active: bool | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    text_and_rank_visibility: str = "restricted_in_anonymous_view"
    text: str | None = None
    rank: int | None = None


class BattleOrdersResult(ToolResult):
    battle_id: str
    side: str
    orders: list[BattleOrder]
    snapshot_count: int
    offset: int
    limit: int
    has_more: bool


class LootEntry(DomainModel):
    type: str | None = None
    item_code: str | None = None
    numeric_values: dict[str, float] = Field(default_factory=dict)
    references: dict[str, str] = Field(default_factory=dict)


class BattleLootResult(ToolResult):
    id: str
    battle_id: str
    user_id: str
    case_counts: dict[str, float] | None = None
    hits: float | None = None
    total_damages: float | None = None
    total_money_from_bounty: float | None = None
    total_money_from_contract: float | None = None
    pool_loot: list[LootEntry] | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    partial: bool = False


class MercenaryBid(DomainModel):
    military_unit_id: str | None = None
    user_id: str | None = None
    per_k: float | None = None
    payout: float | None = None
    bid_at: UtcDateTime | None = None


class MercenaryAuction(DomainModel):
    round_id: str | None = None
    round_number: int | None = None
    id: str
    country_id: str | None = None
    created_by_user_id: str | None = None
    battle_id: str | None = None
    for_country_id: str | None = None
    for_country_side: str | None = None
    minimum_damage: float | None = None
    budget: float | None = None
    initial_per_k: float | None = None
    current_per_k: float | None = None
    current_payout: float | None = None
    duration: float | None = None
    professionals_only: bool | None = None
    expires_at: UtcDateTime | None = None
    status: str | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    current_winner_military_unit_id: str | None = None
    current_winner_user_id: str | None = None
    bids: list[MercenaryBid] | None = None
    bid_count: int | None = None
    bids_truncated: bool = False
    additional_numeric_fields: dict[str, float] = Field(default_factory=dict)


class MercenaryAuctionsResult(ToolResult):
    auctions: list[MercenaryAuction]
    page: PageInfo
    bids_included: bool
    partial: bool = False


class GovernmentResult(ToolResult):
    id: str
    country_id: str
    roles: dict[str, str]
    congress_member_ids: list[str] | None = None
    congress_member_count: int | None = None
    offset: int
    limit: int
    has_more: bool
    activity_dates: dict[str, UtcDateTime] = Field(default_factory=dict)
    announcement_created_ats: list[UtcDateTime] | None = None
    dates_truncated: bool = False


class GlobalRankingEntry(DomainModel):
    entity_id: str
    country_id: str | None = None
    military_unit_id: str | None = None
    rank: int | None = None
    value: float | None = None
    tier: str | None = None


class GlobalRankingResult(ToolResult):
    ranking_type: str
    entity_type: str
    ranking_id: str | None = None
    is_global: bool | None = None
    entries: list[GlobalRankingEntry]
    tier_values: dict[str, float] | None = None
    snapshot_count: int
    offset: int
    limit: int
    has_more: bool


class WorkOfferResult(ToolResult):
    offer: WorkOffer


class Worker(DomainModel):
    id: str
    user_id: str | None = None
    company_id: str | None = None
    employer_id: str | None = None
    wage: float | None = None
    fidelity: float | None = None
    joined_at: UtcDateTime | None = None
    locked_until: UtcDateTime | None = None
    last_fidelity_increase_at: UtcDateTime | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None


class CompanyWorkers(DomainModel):
    company_id: str
    company_name: str | None = None
    item_code: str | None = None
    worker_count: int


class WorkersResult(ToolResult):
    scope: str
    scope_id: str
    workers: list[Worker]
    companies: list[CompanyWorkers] = Field(default_factory=list)
    company_snapshot_count: int | None = None
    companies_truncated: bool = False
    snapshot_count: int
    reported_total_workers_count: int | None = None
    offset: int
    limit: int
    has_more: bool
    partial: bool = False


class Transaction(DomainModel):
    id: str
    transaction_type: str | None = None
    item_code: str | None = None
    money: float | None = None
    quantity: float | None = None
    buyer_user_id: str | None = None
    seller_user_id: str | None = None
    buyer_military_unit_id: str | None = None
    seller_military_unit_id: str | None = None
    buyer_country_id: str | None = None
    seller_country_id: str | None = None
    buyer_party_id: str | None = None
    seller_party_id: str | None = None
    item: EquipmentItem | None = None
    created_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    offer_created_at: UtcDateTime | None = None
    additional_numeric_fields: dict[str, float] = Field(default_factory=dict)


class TransactionsResult(ToolResult):
    transactions: list[Transaction]
    page: PageInfo
    partial: bool = False


class CountryPlayerReference(DomainModel):
    user_id: str
    created_at: UtcDateTime | None = None


class CountryPlayersResult(ToolResult):
    country_id: str
    players: list[CountryPlayerReference]
    page: PageInfo
    partial: bool = False


class EntitySearchResult(ToolResult):
    search: str
    entity_ids: dict[str, list[str]]
    snapshot_counts: dict[str, int]
    has_more: dict[str, bool]
    has_data: bool | None = None
    offset: int
    limit: int
