"""Normalization from upstream WarEra payloads into canonical domain models.

Every function in this module follows the same contract:

* Input is untrusted JSON of unknown shape; output is a canonical model.
* Missing or invalid *important* fields produce a :class:`str` warning instead of
  a fabricated default. ``None`` therefore always means "not observed".
* Percentage-point source fields exposed by the API are converted to fractions
  (``0.41`` for ``41``) so downstream arithmetic is unit-safe.
* Free-form game text (usernames, offer text, event data) is sanitized and
  length-capped before it is exposed; it is never interpreted as instructions.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from warera_mcp.domain.enums import EVENT_TYPES, canonical_enum
from warera_mcp.domain.models import (
    BattleDetail,
    BattleLiveStatus,
    BattleRankingEntry,
    BattleSideRef,
    BattleSummary,
    CompanyDetail,
    CompanySummary,
    CountryFacts,
    Deposit,
    EntityRanking,
    EventSummary,
    OrderBookDepth,
    OrderBookLevel,
    PageInfo,
    PlayerProfile,
    PlayerSkill,
    ProductionBonus,
    RegionDetail,
    RegionSummary,
    RoundSummary,
    WageStats,
    WorkOffer,
)
from warera_mcp.warera.schemas import (
    MAX_CURSOR_LENGTH,
    Record,
    as_mapping,
    as_sequence,
    clean_name,
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_str,
    extract_ident,
    extract_name,
    parse_timestamp,
    record_of,
)

# --------------------------------------------------------------------------- text
_TAG = re.compile(r"<[^>]{0,200}>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
#: Cursors are opaque tokens echoed back to the caller, so *any* control character
#: (newline and tab included) disqualifies one.
_CURSOR_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f-\x9f]")

MAX_EXTERNAL_TEXT = 280


def sanitize_external_text(value: object, *, max_length: int = MAX_EXTERNAL_TEXT) -> str | None:
    """Strip markup/control characters from untrusted game text and cap length.

    The result is still untrusted content; callers must treat it as data and keep
    it clearly separated from instructions.
    """
    text = coerce_str(value)
    if text is None:
        return None
    text = _TAG.sub(" ", text)
    text = _CONTROL.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return None
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "\u2026"
    return text


def percent_points_to_fraction(value: object) -> float | None:
    """Convert a percentage-point value (``41``) to a fraction (``0.41``)."""
    number = coerce_float(value)
    return None if number is None else number / 100.0


# ------------------------------------------------------------------------ picking
def _pick(record: Record, *keys: str) -> Any:
    for key in keys:
        if record.has(key):
            return record.raw(key)
    return None


def _ident(record: Record, *keys: str) -> str | None:
    return extract_ident(_pick(record, *keys))


def _number(record: Record, *keys: str) -> float | None:
    return coerce_float(_pick(record, *keys))


def _integer(record: Record, *keys: str) -> int | None:
    return coerce_int(_pick(record, *keys))


def _text(record: Record, *keys: str) -> str | None:
    """Short upstream text (names, codes, labels): sanitized and length-capped."""
    return clean_name(_pick(record, *keys))


def _cursor(record: Record, *keys: str) -> str | None:
    """Opaque pagination cursor: kept verbatim but bounded and control-free."""
    value = coerce_str(_pick(record, *keys))
    if value is None or len(value) > MAX_CURSOR_LENGTH or _CURSOR_FORBIDDEN.search(value):
        return None
    return value


def _flag(record: Record, *keys: str) -> bool | None:
    return coerce_bool(_pick(record, *keys))


def _timestamp(record: Record, *keys: str) -> datetime | None:
    for key in keys:
        if record.has(key):
            parsed, _ = parse_timestamp(record.raw(key))
            if parsed is not None:
                return parsed
    return None


def _numeric_map(record: Record, *keys: str) -> dict[str, float] | None:
    for key in keys:
        mapping = as_mapping(record.raw(key))
        if mapping is None:
            continue
        values: dict[str, float] = {}
        for name, raw in mapping.items():
            number = coerce_float(raw)
            if number is not None:
                values[str(name)] = number
        if values:
            return values
    return None


def _id_list(record: Record, *keys: str) -> list[str] | None:
    for key in keys:
        sequence = as_sequence(record.raw(key))
        if sequence is None:
            continue
        values = [ident for item in sequence if (ident := extract_ident(item)) is not None]
        return values
    return None


def _child_records(record: Record, *keys: str) -> list[Record]:
    for key in keys:
        children = record.objects(key)
        if children:
            return children
    return []


# ------------------------------------------------------------------------- helpers
def page_info(payload: object) -> PageInfo:
    """Extract an opaque cursor and a best-effort continuation hint."""
    record = record_of(payload)
    cursor = _cursor(record, "nextCursor", "next_cursor", "cursor")
    return PageInfo(next_cursor=cursor, has_more=cursor is not None)


def items_of(payload: object) -> list[Record]:
    """Return the ``items`` array of a paginated payload."""
    record = record_of(payload)
    return record.objects("items")


def normalize_skill_map(record: Record, *keys: str) -> dict[str, float] | None:
    return _numeric_map(record, *keys)


_PROFILE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
PROFILE_MAP_CAP = 64


def profile_numeric_map(payload: object) -> dict[str, float] | None:
    mapping = as_mapping(payload)
    if mapping is None:
        return None
    result: dict[str, float] = {}
    for key, raw in list(mapping.items())[:PROFILE_MAP_CAP]:
        number = coerce_float(raw)
        if _PROFILE_CODE.fullmatch(key) and number is not None and math.isfinite(number):
            result[key] = number
    return result or None


def _profile_details(
    record: Record,
) -> tuple[dict[str, PlayerSkill], dict[str, EntityRanking], dict[str, float], dict[str, float]]:
    skills: dict[str, PlayerSkill] = {}
    rankings: dict[str, EntityRanking] = {}
    skill_values: dict[str, float] = {}
    ranking_values: dict[str, float] = {}
    skill_fields = {
        "level": "level",
        "current_bar_value": "currentBarValue",
        "value": "value",
        "weapon": "weapon",
        "equipment": "equipment",
        "overflow": "overflow",
        "limited": "limited",
        "total": "total",
        "total_after_soft_cap": "totalAfterSoftCap",
        "hourly_bar_regen": "hourlyBarRegen",
        "prestige": "prestige",
    }
    for key, values in (("skills", skill_values), ("rankings", ranking_values)):
        mapping = as_mapping(record.raw(key))
        if mapping is None:
            continue
        if len(mapping) > PROFILE_MAP_CAP:
            record.problems.append(f"$.player.{key}: truncated to 64 entries")
        for code, raw in list(mapping.items())[:PROFILE_MAP_CAP]:
            if not _PROFILE_CODE.fullmatch(code):
                record.problems.append(f"$.player.{key}: invalid field name was omitted")
                continue
            number = coerce_float(raw)
            if number is not None and math.isfinite(number):
                values[code] = number
                continue
            child = as_mapping(raw)
            if child is None:
                record.problems.append(f"$.player.{key}: invalid entry was omitted")
                continue
            numeric = profile_numeric_map(child) or {}
            if key == "skills":
                skill = PlayerSkill(
                    level=coerce_int(numeric.get("level")),
                    prestige=coerce_int(numeric.get("prestige")),
                    current_bar_value=numeric.get("currentBarValue"),
                    value=numeric.get("value"),
                    weapon=numeric.get("weapon"),
                    equipment=numeric.get("equipment"),
                    overflow=numeric.get("overflow"),
                    limited=numeric.get("limited"),
                    total=numeric.get("total"),
                    total_after_soft_cap=numeric.get("totalAfterSoftCap"),
                    hourly_bar_regen=numeric.get("hourlyBarRegen"),
                    modifiers_fraction={
                        wire.removesuffix("Percent"): value / 100
                        for wire, value in numeric.items()
                        if wire.endswith("Percent")
                    },
                    additional_numeric_components={
                        wire: value
                        for wire, value in numeric.items()
                        if wire not in skill_fields.values() and not wire.endswith("Percent")
                    },
                )
                skills[code] = skill
                summary = skill.total if skill.total is not None else skill.value
                if summary is not None:
                    values[code] = summary
            else:
                row = record_of(child)
                rank = coerce_int(numeric.get("rank"))
                value = numeric.get("value")
                rankings[code] = EntityRanking(value=value, rank=rank, tier=row.opt_str("tier"))
                if value is not None:
                    values[code] = value
    return skills, rankings, skill_values, ranking_values


def _profile_dates(record: Record) -> tuple[dict[str, datetime], dict[str, list[datetime]]]:
    dates: dict[str, datetime] = {}
    lists: dict[str, list[datetime]] = {}
    child = record.child("dates")
    if child is not None:
        for key, raw in list(child.data.items())[:PROFILE_MAP_CAP]:
            if not _PROFILE_CODE.fullmatch(key):
                continue
            values = as_sequence(raw)
            if values is not None:
                parsed_values: list[datetime] = []
                for value in values[:20]:
                    parsed, naive = parse_timestamp(value)
                    if parsed is not None:
                        parsed_values.append(parsed)
                    if parsed is None or naive:
                        record.problems.append("$.player.dates: invalid or timezone-less timestamp")
                lists[key] = parsed_values
                if len(values) > 20:
                    record.problems.append("$.player.dates: date list truncated to 20 entries")
            else:
                timestamp = child.timestamp(key)
                if timestamp is not None:
                    dates[key] = timestamp
    return dates, lists


# ------------------------------------------------------------------------- players
def _profile_ident_map(record: Record, key: str) -> dict[str, str] | None:
    mapping = as_mapping(record.raw(key))
    if mapping is None:
        return None
    if len(mapping) > PROFILE_MAP_CAP:
        record.problems.append(f"$.player.{key}: truncated to 64 entries")
    result: dict[str, str] = {}
    for code, value in list(mapping.items())[:PROFILE_MAP_CAP]:
        ident = extract_ident(value)
        if _PROFILE_CODE.fullmatch(code) and ident is not None:
            result[code] = ident
    return result or None


def normalize_player_lite(
    payload: object,
    *,
    default_id: str | None = None,
) -> tuple[PlayerProfile, list[str]]:
    """Normalize ``user.getUserLite`` into a public profile.

    ``default_id`` is the identifier the caller asked for; it is used when the
    upstream record omits its own id, so a successful lookup never degrades into
    an empty id.
    """
    record = record_of(payload, "$.player")
    warnings: list[str] = []
    player_id = _ident(record, "_id", "id", "userId") or default_id
    if player_id is None:
        warnings.append("player id was missing from the upstream profile")
    level = _integer(record, "level")
    if level is None:
        leveling = record.child("leveling")
        if leveling is not None:
            level = _integer(leveling, "level", "value")
    for group in ("leveling", "stats", "dates"):
        mapping = as_mapping(record.raw(group))
        if mapping is not None and len(mapping) > PROFILE_MAP_CAP:
            record.problems.append(f"$.player.{group}: truncated to 64 entries")
    statistics = record.child("stats")
    wealth = statistics.child("wealth") if statistics else None
    if wealth and len(wealth.data) > PROFILE_MAP_CAP:
        record.problems.append("$.player.stats.wealth: truncated to 64 entries")
    skills, rankings, skill_values, ranking_values = _profile_details(record)
    dates, date_lists = _profile_dates(record)
    missions = record.child("missions")
    claimed = missions.child("claimedAt") if missions else None
    mission_dates = (
        {
            code: stamp
            for code in list(claimed.data.keys())[:PROFILE_MAP_CAP]
            if _PROFILE_CODE.fullmatch(code) and (stamp := claimed.timestamp(code)) is not None
        }
        if claimed
        else {}
    )
    tours = record.child("finishedTours")
    finished_tours = (
        {
            code: value
            for code in list(tours.data.keys())[:PROFILE_MAP_CAP]
            if _PROFILE_CODE.fullmatch(code) and (value := tours.boolean(code)) is not None
        }
        if tours
        else {}
    )
    for child, name in (
        (missions, "missions"),
        (claimed, "missions.claimedAt"),
        (tours, "finishedTours"),
    ):
        if child and len(list(child.data.keys())) > PROFILE_MAP_CAP:
            record.problems.append(f"$.player.{name}: truncated to 64 entries")
    profile = PlayerProfile(
        id=player_id or "",
        username=_text(record, "username", "name"),
        level=level,
        country_id=_ident(record, "country", "countryId"),
        region_id=_ident(record, "region", "regionId"),
        skills=skill_values or None,
        rankings=ranking_values or None,
        skill_details=skills or None,
        ranking_details=rankings or None,
        military_unit_id=_ident(record, "mu", "militaryUnit"),
        military_rank=record.integer("militaryRank"),
        is_active=record.boolean("isActive"),
        created_at=record.timestamp("createdAt"),
        leveling=profile_numeric_map(record.raw("leveling")),
        stats=profile_numeric_map(record.raw("stats")),
        wealth_breakdown=profile_numeric_map(wealth.data) if wealth else None,
        activity_dates=dates or None,
        activity_date_lists=date_lists or None,
        location_id=_ident(record, "location"),
        company_id=_ident(record, "company"),
        party_id=_ident(record, "party"),
        updated_at=record.timestamp("updatedAt"),
        military_unit_max_level_rewarded=record.integer("muMaxLevelRewarded"),
        equipment_ids=_profile_ident_map(record, "equipment"),
        mission_statistics=profile_numeric_map(record.raw("missions")),
        mission_claimed_at=mission_dates or None,
        finished_tours=finished_tours or None,
    )
    return profile, list(dict.fromkeys([*warnings, *record.problems]))


# ----------------------------------------------------------------------- companies
def normalize_company_detail(
    payload: object,
    *,
    default_id: str | None = None,
) -> tuple[CompanyDetail, list[str]]:
    """Normalize ``company.getById`` into canonical company facts."""
    record = record_of(payload, "$.company")
    warnings: list[str] = []
    company_id = _ident(record, "_id", "id", "companyId") or default_id
    if company_id is None:
        warnings.append("company id was missing from the upstream record")
    detail = CompanyDetail(
        id=company_id or "",
        name=_text(record, "name"),
        owner_id=_ident(record, "user", "userId", "owner"),
        region_id=_ident(record, "region", "regionId"),
        item_code=_text(record, "itemCode", "item_code"),
        stored_production=_number(record, "production", "storedProduction"),
        storage_level=_integer(record, "storage", "storageLevel"),
        automated_engine_level=_integer(
            record, "automatedEngine", "automatedEngineLevel", "engineLevel"
        ),
        worker_count=_integer(record, "workerCount", "workers"),
        created_at=_timestamp(record, "createdAt", "created_at"),
    )
    if detail.item_code is None:
        warnings.append("company item_code was not present in the upstream record")
    return detail, warnings


def normalize_company_summary(payload: object, *, default_id: str | None = None) -> CompanySummary:
    """Normalize one company detail payload into the compact list projection."""
    detail, _ = normalize_company_detail(payload, default_id=default_id)
    record = record_of(payload, "$.company")
    return CompanySummary(
        id=detail.id,
        name=detail.name,
        item_code=detail.item_code,
        region_id=detail.region_id,
        worker_count=detail.worker_count,
        production_stored=detail.stored_production,
        upgrade_levels=_numeric_map(record, "activeUpgradeLevels", "upgradeLevels"),
    )


#: Mapping of upstream production-bonus component keys to canonical names.
BONUS_COMPONENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("strategic", ("strategicBonus",)),
    ("deposit", ("depositBonus",)),
    ("ethic_specialization", ("ethicSpecializationBonus",)),
    ("ethic_deposit", ("ethicDepositBonus",)),
)


def normalize_production_bonus(payload: object) -> tuple[ProductionBonus, list[str]]:
    """Normalize ``company.getProductionBonus`` percentage points to fractions."""
    record = record_of(payload, "$.production_bonus")
    warnings: list[str] = []
    components: dict[str, float] = {}
    for canonical_name, source_keys in BONUS_COMPONENTS:
        fraction = percent_points_to_fraction(_pick(record, *source_keys))
        if fraction is not None:
            components[canonical_name] = fraction
    total = percent_points_to_fraction(_pick(record, "total", "totalBonus"))
    if total is None and components:
        total = sum(components.values())
        warnings.append("production bonus total was absent; summed observed components")
    if total is None:
        warnings.append("production bonus payload contained no recognizable components")
    return ProductionBonus(total_fraction=total, components=components), warnings


# --------------------------------------------------------------------------- world
def normalize_country(
    payload: object,
    *,
    default_id: str | None = None,
) -> tuple[CountryFacts, list[str]]:
    """Normalize ``country.getAllCountries`` / ``getCountryById`` records."""
    record = record_of(payload, "$.country")
    warnings: list[str] = []
    country_id = _ident(record, "_id", "id", "countryId") or default_id
    if country_id is None:
        warnings.append("country id was missing from the upstream record")
    taxes = _numeric_map(record, "taxes", "taxRates")
    facts = CountryFacts(
        id=country_id or "",
        name=_text(record, "name"),
        code=_text(record, "code", "countryCode"),
        population=_integer(record, "population"),
        development=_number(record, "development"),
        taxes=taxes,
        specialized_item=_text(
            record,
            "specializedItem",
            "specializedItemCode",
            "specialization",
            "specializedResource",
        ),
        wars_with=_id_list(record, "warsWith", "wars_with"),
        allies=_id_list(record, "allies"),
        alliance_id=_ident(record, "allianceId", "alliance"),
        enemy=_ident(record, "enemy"),
    )
    return facts, warnings


def normalize_region_summary(payload: object) -> RegionSummary:
    """Normalize a region record into the compact country-overview projection."""
    record = record_of(payload, "$.region")
    return RegionSummary(
        id=_ident(record, "_id", "id", "regionId") or "",
        name=_text(record, "name"),
        population=_integer(record, "population"),
        development=_number(record, "development"),
        climate=_text(record, "climate"),
        biome=_text(record, "biome"),
    )


def normalize_region_detail(
    payload: object,
    *,
    default_id: str | None = None,
) -> tuple[RegionDetail, list[str]]:
    """Normalize ``region.getById`` into canonical region detail."""
    record = record_of(payload, "$.region")
    warnings: list[str] = []
    region_id = _ident(record, "_id", "id", "regionId") or default_id
    if region_id is None:
        warnings.append("region id was missing from the upstream record")

    deposit: Deposit | None = None
    deposit_record = record.child("deposit")
    if deposit_record is not None:
        deposit = Deposit(
            type=_text(deposit_record, "type"),
            bonus_fraction=percent_points_to_fraction(
                _pick(deposit_record, "bonusPercent", "bonus")
            ),
            starts_at=_timestamp(deposit_record, "startsAt", "starts_at"),
            ends_at=_timestamp(deposit_record, "endsAt", "ends_at"),
        )
        if deposit.bonus_fraction is None:
            warnings.append("region deposit bonusPercent was absent or non-numeric")

    detail = RegionDetail(
        id=region_id or "",
        name=_text(record, "name"),
        country_id=_ident(record, "country", "countryId"),
        is_capital=_flag(record, "isCapital", "capital"),
        climate=_text(record, "climate"),
        biome=_text(record, "biome"),
        population=_integer(record, "population"),
        development=_number(record, "development"),
        deposit=deposit,
        neighbors=_id_list(record, "neighbors", "neighbours"),
        upgrades=_numeric_map(record, "upgrades", "activeUpgradeLevels"),
    )
    return detail, warnings


# --------------------------------------------------------------------------- market
def normalize_prices(payload: object) -> dict[str, float]:
    """Normalize ``itemTrading.getPrices`` into an ``item_code -> price`` map."""
    mapping = as_mapping(payload)
    if mapping is None:
        return {}
    prices: dict[str, float] = {}
    for item_code, raw in mapping.items():
        price = coerce_float(raw)
        if price is not None:
            prices[str(item_code)] = price
    return prices


def _normalize_order_level(record: Record) -> OrderBookLevel | None:
    price = _number(record, "price")
    if price is None:
        return None
    return OrderBookLevel(
        price=price,
        quantity=_number(record, "quantity", "amount"),
        offer_at=_timestamp(record, "offerAt", "createdAt", "updatedAt"),
    )


def normalize_order_book(
    payload: object,
    *,
    max_orders: int,
    include_buy: bool = True,
    include_sell: bool = True,
) -> tuple[list[OrderBookLevel], list[OrderBookLevel]]:
    """Normalize ``tradingOrder.getTopOrders``, sorting defensively by price."""
    record = record_of(payload, "$.order_book")
    buy: list[OrderBookLevel] = []
    sell: list[OrderBookLevel] = []
    if include_buy:
        buy = [
            level
            for child in _child_records(record, "buyOrders", "buys")
            if (level := _normalize_order_level(child)) is not None
        ]
        buy.sort(key=lambda level: level.price, reverse=True)
        buy = buy[:max_orders]
    if include_sell:
        sell = [
            level
            for child in _child_records(record, "sellOrders", "sells")
            if (level := _normalize_order_level(child)) is not None
        ]
        sell.sort(key=lambda level: level.price)
        sell = sell[:max_orders]
    return buy, sell


def compute_depth(levels: list[OrderBookLevel], target_quantity: float) -> OrderBookDepth | None:
    """Aggregate visible depth up to *target_quantity* as a best-effort VWAP."""
    if target_quantity <= 0 or not levels:
        return None
    remaining = target_quantity
    filled = 0.0
    notional = 0.0
    for level in levels:
        quantity = level.quantity
        if quantity is None or quantity <= 0:
            continue
        take = min(quantity, remaining)
        filled += take
        notional += take * level.price
        remaining -= take
        if remaining <= 0:
            break
    if filled <= 0:
        return None
    return OrderBookDepth(quantity=filled, estimated_vwap=notional / filled)


# ----------------------------------------------------------------------------- work
def _normalize_wage_value(record: Record, *keys: str) -> float | None:
    for key in keys:
        if not record.has(key):
            continue
        raw = record.raw(key)
        number = coerce_float(raw)
        if number is not None:
            return number
        nested = as_mapping(raw)
        if nested is not None:
            inner = coerce_float(nested.get("wageAfterTax"))
            if inner is None:
                inner = coerce_float(nested.get("wage"))
            if inner is not None:
                return inner
    return None


def normalize_wage_stats(payload: object) -> WageStats:
    """Normalize ``workOffer.getWageStats`` gross/net-agnostic benchmarks."""
    record = record_of(payload, "$.wage_stats")
    allowed = record.child("allowedRange") or record.child("range")
    top_eligible = _child_records(record, "topEligibleOffers")
    offers: list[float] = []
    for child in top_eligible:
        value = _normalize_wage_value(child, "wage", "wageAfterTax")
        if value is not None:
            offers.append(value)
    return WageStats(
        min=_number(allowed, "min") if allowed else None,
        max=_number(allowed, "max") if allowed else None,
        average=_number(allowed, "average", "avg") if allowed else None,
        top_offer=_normalize_wage_value(record, "topOffer"),
        top_eligible_offer=_normalize_wage_value(record, "topEligibleOffer"),
        top_eligible_offers=offers or None,
    )


def normalize_work_offer(payload: object) -> WorkOffer:
    """Normalize one ``workOffer.getWorkOffersPaginated`` row."""
    record = record_of(payload, "$.work_offer")
    return WorkOffer(
        id=_ident(record, "_id", "id"),
        user_id=_ident(record, "user", "userId"),
        initial_quantity=_number(record, "initialQuantity"),
        created_at=record.timestamp("createdAt"),
        updated_at=record.timestamp("updatedAt"),
        company_id=_ident(record, "company", "companyId"),
        region_id=_ident(record, "region", "regionId"),
        quantity=_number(record, "quantity"),
        wage=_number(record, "wage"),
        wage_after_tax=_number(record, "wageAfterTax", "wage_after_tax"),
        minimum_energy=record.num("minEnergy"),
        minimum_production=record.num("minProduction"),
        minimum_level=record.num("minLevel"),
        citizenship=_text(record, "citizenship"),
        text=sanitize_external_text(_pick(record, "text", "description")),
    )


# -------------------------------------------------------------------------- battles
def _normalize_side(
    record: Record | None,
    *,
    country_names: dict[str, str] | None = None,
) -> BattleSideRef | None:
    if record is None:
        return None
    country_id = _ident(record, "country", "countryId")
    return BattleSideRef(
        country_id=country_id,
        country_name=country_names.get(country_id) if country_names and country_id else None,
        region_id=_ident(record, "region", "regionId"),
        damages=_number(record, "damages", "damage"),
        won_rounds=_integer(record, "wonRoundsCount", "wonRounds", "won_rounds"),
        hit_count=record.integer("hitCount"),
        military_unit_ids_with_orders=(
            record.strings("muOrders")[:20] if record.has("muOrders") else None
        ),
        country_ids_with_orders=record.strings("countryOrders")[:20]
        if record.has("countryOrders")
        else None,
        orders_truncated=len(record.strings("muOrders")) > 20
        or len(record.strings("countryOrders")) > 20,
    )


def _normalize_round(record: Record) -> RoundSummary:
    points = record.child("points")
    attacker = record.child("attacker")
    defender = record.child("defender")
    live = record.child("live")
    return RoundSummary(
        round_id=_ident(record, "_id", "id", "roundId"),
        battle_id=_ident(record, "battle", "battleId"),
        number=record.integer("number"),
        is_active=record.boolean("isActive"),
        attacker_country_id=attacker.ident("country") if attacker else None,
        defender_country_id=defender.ident("country") if defender else None,
        attacker_hit_count=attacker.integer("hitCount") if attacker else None,
        defender_hit_count=defender.integer("hitCount") if defender else None,
        ticks_count=live.integer("ticksCount") if live else record.integer("ticksCount"),
        actual_tick_points=live.num("actualTickPoints") if live else record.num("actualTickPoints"),
        created_at=record.timestamp("createdAt"),
        updated_at=record.timestamp("updatedAt"),
        attacker_damages=attacker.num("damages")
        if attacker
        else _number(record, "attackerDamages", "attackerDamage"),
        defender_damages=defender.num("damages")
        if defender
        else _number(record, "defenderDamages", "defenderDamage"),
        attacker_points=attacker.num("points")
        if attacker
        else (_number(points, "attacker") if points else record.num("attackerPoints")),
        defender_points=defender.num("points")
        if defender
        else (_number(points, "defender") if points else record.num("defenderPoints")),
        next_tick_at=live.timestamp("nextTickAt")
        if live
        else _timestamp(record, "nextTickAt", "next_tick_at"),
    )


def _battle_identity(record: Record) -> str | None:
    return _ident(record, "_id", "id", "battleId")


def normalize_battle_summary(
    payload: object,
    *,
    country_names: dict[str, str] | None = None,
) -> BattleSummary:
    """Normalize one ``battle.getBattles`` row into a compact summary."""
    record = record_of(payload, "$.battle")
    current_round = _ident(record, "currentRound", "currentRoundId", "round")
    if current_round is None:
        nested = record.child("currentRound")
        if nested is not None:
            current_round = _ident(nested, "_id", "id")
    return BattleSummary(
        id=_battle_identity(record) or "",
        type=canonical_enum(
            _pick(record, "type"), {"land", "sea", "air", "war", "resistance", "revolt"}
        ),
        is_active=_flag(record, "isActive", "active"),
        attacker=_normalize_side(record.child("attacker"), country_names=country_names),
        defender=_normalize_side(record.child("defender"), country_names=country_names),
        current_round=current_round,
        war_id=_ident(record, "war", "warId"),
        created_at=_timestamp(record, "createdAt", "startedAt"),
    )


def normalize_battle_detail(
    payload: object,
    *,
    include_history: bool,
    default_id: str | None = None,
    country_names: dict[str, str] | None = None,
) -> tuple[BattleDetail, list[str]]:
    """Normalize ``battle.getById`` into a dossier without volatile payloads."""
    record = record_of(payload, "$.battle")
    warnings: list[str] = []
    battle_id = _battle_identity(record) or default_id
    if battle_id is None:
        warnings.append("battle id was missing from the upstream record")

    current_round: RoundSummary | None = None
    round_child = record.child("currentRound")
    if round_child is not None:
        current_round = _normalize_round(round_child)
    elif _text(record, "currentRound") is not None:
        current_round = RoundSummary(round_id=_text(record, "currentRound"))

    history: list[RoundSummary] | None = None
    if include_history:
        history = [
            _normalize_round(child)
            for child in _child_records(record, "roundsHistory", "roundHistory", "rounds")
        ]

    detail = BattleDetail(
        id=battle_id or "",
        type=canonical_enum(
            _pick(record, "type"), {"land", "sea", "air", "war", "resistance", "revolt"}
        ),
        is_active=_flag(record, "isActive", "active"),
        attacker=_normalize_side(record.child("attacker"), country_names=country_names),
        defender=_normalize_side(record.child("defender"), country_names=country_names),
        current_round=current_round,
        war_id=_ident(record, "war", "warId"),
        rounds_to_win=_integer(record, "roundsToWin", "rounds_to_win"),
        created_at=_timestamp(record, "createdAt", "startedAt"),
        updated_at=record.timestamp("updatedAt"),
        round_history=history,
        round_ids=record.strings("rounds") if record.has("rounds") else None,
    )
    if detail.attacker is None or detail.defender is None:
        warnings.append("battle sides were only partially present in the upstream record")
    return detail, warnings


def normalize_live_battle(payload: object, *, include_history: bool = False) -> BattleLiveStatus:
    """Normalize ``battle.getLiveBattleData`` ``{battle, round}`` payloads."""
    record = record_of(payload, "$.live")
    round_record = record.child("round") or record.child("currentRound") or record
    battle = record.child("battle")
    return BattleLiveStatus(
        **_normalize_round(round_record).model_dump(),
        battle_is_active=battle.boolean("isActive") if battle else None,
        round_ids=battle.strings("roundIds") if battle and battle.has("roundIds") else None,
        attacker_country_ids_with_orders=(
            battle.strings("attackerCountryOrders")
            if battle and battle.has("attackerCountryOrders")
            else None
        ),
        defender_country_ids_with_orders=(
            battle.strings("defenderCountryOrders")
            if battle and battle.has("defenderCountryOrders")
            else None
        ),
        round_history=[_normalize_round(child) for child in battle.objects("roundHistory")]
        if battle and include_history and battle.has("roundHistory")
        else None,
    )


def normalize_battle_ranking_entry(payload: object, *, entity_type: str) -> BattleRankingEntry:
    """Normalize one ``battleRanking.getRanking`` row for the requested type.

    The entity slot is either a bare id or an embedded object. A bare string is
    an identifier, never a display name.
    """
    record = record_of(payload, "$.ranking_entry")
    entity_raw = record.raw(entity_type)
    entity_id = extract_ident(entity_raw) or _ident(record, "entityId", "entity")
    name: str | None = None
    if isinstance(entity_raw, Mapping):
        name = extract_name(entity_raw)
    if name is None:
        name = extract_name(_pick(record, "name", "username"))
    return BattleRankingEntry(
        rank=_integer(record, "rank"),
        entity_id=entity_id,
        name=name,
        value=_number(record, "value", "damage", "points", "score"),
    )


# --------------------------------------------------------------------------- events
_EVENT_TEXT_KEYS = ("title", "message", "text", "description", "name")

#: Event kinds observed or documented. Unknown values are preserved sanitized.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "battle",
        "war",
        "peace",
        "alliance",
        "election",
        "revolution",
        "region",
        "company",
        "market",
        "transaction",
        "trade",
        "upgrade",
        "deposit",
        "resistance",
        "referral",
        "level",
        "achievement",
        "article",
    }
)


def _collect_related_ids(source: Record) -> list[str]:
    """Collect distinct related entity ids from an event record in stable order."""
    found: list[str] = []
    for key in (
        "countries",
        "countryIds",
        "countryId",
        "userIds",
        "userId",
        "muIds",
        "regionIds",
        "battle",
        "war",
        "mu",
        "user",
        "company",
        "country",
        "region",
        "attackerCountry",
        "defenderCountry",
        "attackerRegion",
        "defenderRegion",
    ):
        values = _id_list(source, key)
        if values is None:
            single = _ident(source, key)
            values = [single] if single is not None else []
        for ident in values:
            if ident not in found:
                found.append(ident)
    return found


def normalize_event(payload: object) -> EventSummary:
    """Normalize one ``event.getEventsPaginated`` row; text stays untrusted."""
    record = record_of(payload, "$.event")
    data = record.child("data")
    event_type = canonical_enum(
        _pick(record, "type") or (_pick(data, "type") if data else None),
        KNOWN_EVENT_TYPES | {code.lower() for code in EVENT_TYPES},
    )
    related: list[str] = []
    for source in (record, data):
        if source is None:
            continue
        for ident in _collect_related_ids(source):
            if ident not in related:
                related.append(ident)

    summary: str | None = None
    for source in (data, record):
        if source is None:
            continue
        summary = sanitize_external_text(_pick(source, *_EVENT_TEXT_KEYS))
        if summary:
            break

    return EventSummary(
        id=_ident(record, "_id", "id", "eventId") or "",
        type=event_type,
        occurred_at=_timestamp(record, "createdAt", "occurredAt", "timestamp"),
        related_ids=related,
        summary=summary,
    )


__all__ = [
    "BONUS_COMPONENTS",
    "KNOWN_EVENT_TYPES",
    "MAX_EXTERNAL_TEXT",
    "compute_depth",
    "items_of",
    "normalize_battle_detail",
    "normalize_battle_ranking_entry",
    "normalize_battle_summary",
    "normalize_company_detail",
    "normalize_company_summary",
    "normalize_country",
    "normalize_event",
    "normalize_live_battle",
    "normalize_order_book",
    "normalize_player_lite",
    "normalize_prices",
    "normalize_production_bonus",
    "normalize_region_detail",
    "normalize_region_summary",
    "normalize_wage_stats",
    "normalize_work_offer",
    "page_info",
    "percent_points_to_fraction",
    "sanitize_external_text",
]
