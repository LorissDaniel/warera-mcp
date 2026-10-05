"""Reviewed projections of public and API-key game data; no raw payload passthrough."""

from __future__ import annotations

import re
from datetime import datetime

from warera_mcp.domain.extended_models import (
    BattleHit,
    BattleOrder,
    EntityUpgrade,
    EquipmentItem,
    GovernmentResult,
    LootEntry,
    MercenaryAuction,
    MercenaryBid,
    Transaction,
    Worker,
)
from warera_mcp.domain.normalization import profile_numeric_map, sanitize_external_text
from warera_mcp.warera.errors import WareraSchemaError
from warera_mcp.warera.schemas import Record, as_mapping, as_sequence, extract_ident, record_of


def require_object(payload: object) -> Record:
    if as_mapping(payload) is None:
        raise WareraSchemaError("expected a game object")
    return record_of(payload)


def require_id(record: Record) -> str:
    ident = record.ident("_id")
    if ident is None:
        raise WareraSchemaError("game record has no valid identifier")
    return ident


def object_rows(payload: object) -> list[Record]:
    values = as_sequence(payload)
    if values is None or any(as_mapping(v) is None for v in values):
        raise WareraSchemaError("expected a game object array")
    return [record_of(v) for v in values]


def equipment_item(payload: object) -> EquipmentItem:
    r = require_object(payload)
    return EquipmentItem(
        id=r.ident("_id"),
        code=r.opt_str("code"),
        type=r.opt_str("type"),
        skills=profile_numeric_map(r.raw("skills")),
        state=r.num("state"),
        max_state=r.num("maxState"),
        quantity=r.num("quantity"),
        last_acquisition_at=r.timestamp("lastAcquisitionAt"),
    )


def entity_upgrade(payload: object, entity_type: str, entity_id: str, kind: str) -> EntityUpgrade:
    r = require_object(payload)
    if r.ident(entity_type) != entity_id or r.opt_str("upgradeType") != kind:
        raise WareraSchemaError("upgrade identity does not match request")
    return EntityUpgrade(
        id=require_id(r),
        company_id=r.ident("company"),
        region_id=r.ident("region"),
        upgrade_type=kind,
        level=r.integer("level"),
        status=r.opt_str("status"),
        invested_money=r.num("investedMoney"),
        invested_concrete=r.num("investedConcrete"),
        invested_steel=r.num("investedSteel"),
        dependant_users_count=r.integer("dependantUsersCount"),
        created_at=r.timestamp("createdAt"),
        updated_at=r.timestamp("updatedAt"),
        status_changed_at=r.timestamp("statusChangedAt"),
        will_be_active_at=r.timestamp("willBeActiveAt"),
        last_upgrade_at=r.timestamp("lastUpgradeAt"),
        last_downgrade_at=r.timestamp("lastDowngradeAt"),
    )


def battle_hit(r: Record, include_equipment: bool) -> BattleHit:
    items = object_rows(r.raw("equipments")) if include_equipment and r.has("equipments") else None
    return BattleHit(
        id=r.ident("_id"),
        user_id=r.ident("user"),
        military_unit_id=r.ident("mu"),
        damages=r.num("damages"),
        is_critical_hit=r.boolean("isCriticalHit"),
        is_missed=r.boolean("isMissed"),
        hit_at=r.timestamp("hitAt"),
        weapon=equipment_item(r.raw("weapon")) if include_equipment and r.child("weapon") else None,
        equipment=[equipment_item(v.data) for v in items[:10]] if items is not None else None,
        ammo=r.opt_str("ammo") if include_equipment else None,
        equipment_truncated=bool(items and len(items) > 10),
    )


def battle_order(r: Record, battle_id: str, side: str) -> BattleOrder:
    if r.ident("battle") != battle_id or r.opt_str("side") != side:
        raise WareraSchemaError("battle order identity does not match request")
    # Public calls are anonymous: the documented empty text/rank=0 are restricted sentinels.
    text = sanitize_external_text(r.raw("text"))
    rank = r.integer("rank")
    visible = bool(text) or (rank is not None and rank != 0)
    return BattleOrder(
        id=require_id(r),
        battle_id=battle_id,
        side=side,
        country_id=r.ident("country"),
        military_unit_id=r.ident("mu"),
        user_id=r.ident("user"),
        side_country_id=r.ident("sideCountry"),
        priority=r.opt_str("priority"),
        is_active=r.boolean("isActive"),
        created_at=r.timestamp("createdAt"),
        updated_at=r.timestamp("updatedAt"),
        text=text if visible else None,
        rank=rank if visible else None,
        text_and_rank_visibility="reported" if visible else "restricted_in_anonymous_view",
    )


def loot_entry(r: Record) -> LootEntry:
    refs = {
        k: value
        for k in r.data
        if (k.endswith("Id") or k == "_id") and (value := r.ident(k)) is not None
    }
    return LootEntry(
        type=r.opt_str("type"),
        item_code=r.opt_str("itemCode") or r.opt_str("code"),
        numeric_values=profile_numeric_map({k: v for k, v in r.data.items() if k != "__v"}) or {},
        references=dict(list(refs.items())[:64]),
    )


def mercenary_auction(r: Record, include_bids: bool, bid_limit: int) -> MercenaryAuction:
    bids = object_rows(r.raw("bids")) if r.has("bids") else None
    extra = profile_numeric_map(
        {
            k: v
            for k, v in r.data.items()
            if k
            not in {
                "__v",
                "minimumDamage",
                "budget",
                "initialPerK",
                "currentPerK",
                "currentPayout",
                "duration",
                "roundNumber",
            }
        }
    )
    return MercenaryAuction(
        round_id=r.ident("round"),
        round_number=r.integer("roundNumber"),
        id=require_id(r),
        country_id=r.ident("country"),
        created_by_user_id=r.ident("createdBy"),
        battle_id=r.ident("battle"),
        for_country_id=r.ident("forCountry"),
        for_country_side=r.opt_str("forCountrySide"),
        minimum_damage=r.num("minimumDamage"),
        budget=r.num("budget"),
        initial_per_k=r.num("initialPerK"),
        current_per_k=r.num("currentPerK"),
        current_payout=r.num("currentPayout"),
        duration=r.num("duration"),
        professionals_only=r.boolean("professionalsOnly"),
        expires_at=r.timestamp("expiresAt"),
        status=r.opt_str("status"),
        created_at=r.timestamp("createdAt"),
        updated_at=r.timestamp("updatedAt"),
        current_winner_military_unit_id=r.ident("currentWinner"),
        current_winner_user_id=r.ident("currentWinnerUser"),
        bids=[
            MercenaryBid(
                military_unit_id=v.ident("mu"),
                user_id=v.ident("user"),
                per_k=v.num("perK"),
                payout=v.num("payout"),
                bid_at=v.timestamp("bidAt"),
            )
            for v in bids[:bid_limit]
        ]
        if include_bids and bids is not None
        else None,
        bid_count=len(bids) if bids is not None else None,
        bids_truncated=bool(include_bids and bids and len(bids) > bid_limit),
        additional_numeric_fields=extra or {},
    )


def worker(r: Record, company_id: str | None = None) -> Worker:
    if company_id is not None and r.ident("company") not in (None, company_id):
        raise WareraSchemaError("worker company does not match scope")
    return Worker(
        id=require_id(r),
        user_id=r.ident("user"),
        company_id=r.ident("company") or company_id,
        employer_id=r.ident("employer"),
        wage=r.num("wage"),
        fidelity=r.num("fidelity"),
        joined_at=r.timestamp("joinedAt"),
        locked_until=r.timestamp("lockedUntil"),
        last_fidelity_increase_at=r.timestamp("lastFidelityIncreaseAt"),
        created_at=r.timestamp("createdAt"),
        updated_at=r.timestamp("updatedAt"),
    )


def transaction(r: Record) -> Transaction:
    return Transaction(
        id=require_id(r),
        transaction_type=r.opt_str("transactionType"),
        item_code=r.opt_str("itemCode"),
        money=r.num("money"),
        quantity=r.num("quantity"),
        buyer_user_id=r.ident("buyerId"),
        seller_user_id=r.ident("sellerId"),
        buyer_military_unit_id=r.ident("buyerMuId"),
        seller_military_unit_id=r.ident("sellerMuId"),
        buyer_country_id=r.ident("buyerCountryId"),
        seller_country_id=r.ident("sellerCountryId"),
        buyer_party_id=r.ident("buyerPartyId"),
        seller_party_id=r.ident("sellerPartyId"),
        item=equipment_item(r.raw("item")) if r.child("item") else None,
        created_at=r.timestamp("createdAt"),
        updated_at=r.timestamp("updatedAt"),
        offer_created_at=r.timestamp("offerCreatedAt"),
        additional_numeric_fields=profile_numeric_map(
            {k: v for k, v in r.data.items() if k not in {"__v", "money", "quantity"}}
        )
        or {},
    )


CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def government_snapshot(
    payload: object, *, country_id: str, offset: int, limit: int, observed_at: datetime
) -> GovernmentResult:
    r = require_object(payload)
    if r.ident("country") != country_id:
        raise WareraSchemaError("government country mismatch")
    members = as_sequence(r.raw("congressMembers"))
    if r.has("congressMembers") and members is None:
        raise WareraSchemaError("invalid congress list")
    ids = [extract_ident(v) for v in members] if members is not None else None
    if ids is not None and any(v is None for v in ids):
        raise WareraSchemaError("invalid congress identifier")
    roles = {
        name: ident
        for key, name in (
            ("president", "president"),
            ("vicePresident", "vice_president"),
            ("minOfDefense", "minister_of_defense"),
            ("minOfEconomy", "minister_of_economy"),
            ("minOfForeignAffairs", "minister_of_foreign_affairs"),
        )
        if (ident := r.ident(key)) is not None
    }
    dates = r.child("dates")
    announcements = as_sequence(dates.raw("announcementCreatedAts")) if dates else None
    timestamps = []
    if announcements is not None:
        for value in announcements[:20]:
            stamp = Record({"date": value}, problems=r.problems).timestamp("date")
            if stamp is not None:
                timestamps.append(stamp)
    activity = (
        {
            k: stamp
            for k in list(dates.data)[:64]
            if CODE.fullmatch(k)
            and k != "announcementCreatedAts"
            and (stamp := dates.timestamp(k)) is not None
        }
        if dates
        else {}
    )
    return GovernmentResult(
        observed_at=observed_at,
        warnings=r.problems,
        id=require_id(r),
        country_id=country_id,
        roles=roles,
        congress_member_ids=[v for v in ids[offset : offset + limit] if v is not None]
        if ids is not None
        else None,
        congress_member_count=len(ids) if ids is not None else None,
        offset=offset,
        limit=limit,
        has_more=bool(ids is not None and offset + limit < len(ids)),
        activity_dates=activity,
        announcement_created_ats=timestamps if announcements is not None else None,
        dates_truncated=bool(announcements and len(announcements) > 20)
        or bool(dates and len(dates.data) > 64),
    )
