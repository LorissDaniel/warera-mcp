"""Bounded projections of the official, anonymously fetched game configuration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from warera_mcp import errors as app_errors
from warera_mcp.application.common import UpstreamCaller
from warera_mcp.domain.game_configuration_normalization import (
    content_fingerprint,
    finite_number,
)
from warera_mcp.domain.game_rule_catalog import RULES, TABLE_RULES
from warera_mcp.domain.models import (
    ConfigurationProvenance,
    GameRulesResult,
    GameScheduleResult,
    ItemDetailsResult,
    Recipe,
    RecipeInput,
    SkillProgressionResult,
)
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import as_mapping

CONFIG = "gameConfig.getGameConfig"
DATES = "gameConfig.getDates"
URL = "https://api2.warera.io/trpc/"
TOPICS = {
    "player": "user",
    "combat": "battle",
    "companies": "company",
    "workers": "worker",
    "military_units": "mu",
    "upgrades": "upgradesConfig",
    "politics": "election",
    "world": "region",
    "missions": "mission",
    "items": "items",
    "skills": "skills",
}
SCHEDULE = {
    "nextDayAt": "next_day_at",
    "nextRegenAt": "next_regeneration_at",
    "previousDayAt": "previous_day_at",
    "nextCongressElectionsAt": "next_congress_elections_at",
    "nextPresidentialElectionsAt": "next_presidential_elections_at",
    "nextMonthAt": "next_month_at",
    "dailyMissionRegenAt": "daily_mission_regeneration_at",
    "weeklyMissionRegenAt": "weekly_mission_regeneration_at",
    "monthlyMissionRegenAt": "monthly_mission_regeneration_at",
}


class GameConfigurationService:
    def __init__(self, caller: UpstreamCaller) -> None:
        self._caller = caller

    async def _read(
        self, procedure: str, operation: str, correlation_id: str | None
    ) -> tuple[dict[str, Any], UpstreamRead, ConfigurationProvenance]:
        read = await self._caller.read(
            operation, procedure, credentials=None, correlation_id=correlation_id
        )
        mapping = as_mapping(read.data)
        if mapping is None:
            raise app_errors.upstream_schema_changed(
                "official configuration shape was not recognized", operation, procedure=procedure
            )
        try:
            fingerprint = content_fingerprint(mapping)
        except (TypeError, ValueError):
            raise app_errors.upstream_schema_changed(
                "official configuration contained invalid values", operation, procedure=procedure
            ) from None
        now = datetime.now(UTC)
        provenance = ConfigurationProvenance(
            source_procedure=procedure,
            source_url=URL + procedure,
            configuration_version=fingerprint,
            observed_at=read.observed_at,
            freshness_seconds=max(0, int((now - read.observed_at).total_seconds())),
        )
        return dict(mapping), read, provenance

    async def get_rules(
        self, topic: str, offset: int, limit: int, correlation_id: str | None = None
    ) -> GameRulesResult:
        if topic not in TOPICS:
            raise app_errors.invalid_input(
                "unsupported official configuration topic", "get_game_rules"
            )
        data, read, provenance = await self._read(CONFIG, "get_game_rules", correlation_id)
        field = TOPICS[topic]
        section = as_mapping(data.get(field))
        if section is None:
            raise app_errors.upstream_schema_changed(
                "requested configuration section was unavailable",
                "get_game_rules",
                procedure=CONFIG,
            )
        records: list[dict[str, Any]] = []
        malformed = 0
        if topic == "items":
            records = [
                {
                    "id": str(code),
                    "item_type": (item_type if isinstance(item_type, str) else None),
                }
                for code, v in section.items()
                for item_type in [(as_mapping(v) or {}).get("type")]
            ]
        elif topic == "skills":
            for code, levels in section.items():
                skill = as_mapping(levels) or {}
                rows = as_mapping(skill.get("levels")) or skill
                records.append(
                    {
                        "id": str(code),
                        "minimum_level": min(
                            (int(k) for k in rows if str(k).isdigit()), default=None
                        ),
                        "maximum_level": max(
                            (int(k) for k in rows if str(k).isdigit()), default=None
                        ),
                    }
                )
        elif topic == "upgrades":
            stat_names = {
                "attackBonus",
                "defenseBonus",
                "resistanceGrowthReduction",
                "members",
                "maxProduction",
                "dailyProd",
                "dailyHires",
                "maxWorkers",
            }
            for upgrade_code, upgrade_value in section.items():
                upgrade = as_mapping(upgrade_value)
                levels = as_mapping(upgrade.get("levels")) if upgrade is not None else None
                if levels is None:
                    malformed += 1
                    continue
                for key, level_value in levels.items():
                    if not str(key).isdigit():
                        continue
                    row = as_mapping(level_value)
                    if row is None:
                        malformed += 1
                        continue
                    normalized: dict[str, Any] = {"level": int(key)}
                    for source, dest in (
                        ("steelCost", "steel_cost"),
                        ("constructionPointsCost", "construction_points_cost"),
                        ("maintenanceCost", "maintenance_cost"),
                        ("minimumMaintenanceCost", "minimum_maintenance_cost"),
                        ("maintenanceCostCountryDevScale", "maintenance_cost_country_dev_scale"),
                        ("maintenanceCostRegionDevScale", "maintenance_cost_region_dev_scale"),
                    ):
                        if source in row:
                            integral_cost = source in {
                                "steelCost",
                                "constructionPointsCost",
                                "maintenanceCost",
                                "minimumMaintenanceCost",
                            }
                            numeric_value = finite_number(row[source], integral=integral_cost)
                            if numeric_value is None or numeric_value < 0:
                                malformed += 1
                            else:
                                normalized[dest] = numeric_value
                    stats = as_mapping(row.get("stats"))
                    if stats is not None:
                        normalized_stats: dict[str, int | float] = {}
                        for stat, raw_stat_value in stats.items():
                            if stat not in stat_names:
                                continue
                            number = finite_number(raw_stat_value)
                            if number is None:
                                malformed += 1
                            else:
                                normalized_stats[stat] = number
                        if normalized_stats:
                            normalized["stats"] = normalized_stats
                    for source, dest in (
                        ("canBeDisabled", "can_be_disabled"),
                        ("canDowngrade", "can_downgrade"),
                        ("canBeDestroyed", "can_be_destroyed"),
                    ):
                        if upgrade is not None and isinstance(upgrade.get(source), bool):
                            normalized[dest] = upgrade[source]
                    if upgrade is not None and "pendingDurationHours" in upgrade:
                        duration = finite_number(upgrade["pendingDurationHours"])
                        if duration is None or duration < 0:
                            malformed += 1
                        else:
                            normalized["pending_duration_hours"] = duration
                    records.append(
                        {
                            "id": f"{upgrade_code}.{key}",
                            "upstream_field": f"{field}.{upgrade_code}.levels.{key}",
                            **normalized,
                        }
                    )
            if not records and malformed:
                raise app_errors.upstream_schema_changed(
                    "upgrade configuration contained no usable levels",
                    "get_game_rules",
                    procedure=CONFIG,
                )
        else:
            for rule in RULES:
                if rule.topic != topic:
                    continue
                value: object = data
                for component in rule.upstream_path.split("."):
                    nested = as_mapping(value)
                    value = nested.get(component) if nested is not None else None
                if value is None:
                    continue
                number = finite_number(value)
                if number is None:
                    malformed += 1
                    continue
                records.append(
                    {
                        "id": rule.record_id,
                        "label": rule.label,
                        "value": number,
                        "unit": rule.unit,
                        "upstream_field": rule.upstream_path,
                    }
                )
            for table_topic, table_path, record_prefix, key_column, value_column in TABLE_RULES:
                if table_topic != topic:
                    continue
                table_value: object = data
                for component in table_path.split("."):
                    nested = as_mapping(table_value)
                    table_value = nested.get(component) if nested is not None else None
                table = as_mapping(table_value)
                if table is None:
                    continue
                for key, raw_value in table.items():
                    if not str(key).isdigit():
                        malformed += 1
                        continue
                    number = finite_number(raw_value)
                    if number is None or number < 0:
                        malformed += 1
                        continue
                    records.append(
                        {
                            "id": f"{record_prefix}.{key}",
                            key_column: int(key),
                            value_column: number,
                            "upstream_field": f"{table_path}.{key}",
                        }
                    )
            if malformed and not records:
                raise app_errors.upstream_schema_changed(
                    "configuration rule values were malformed", "get_game_rules", procedure=CONFIG
                )
        records.sort(key=lambda row: row["id"])
        return GameRulesResult(
            observed_at=read.observed_at,
            provenance=provenance,
            topic=topic,
            records=records[offset : offset + limit],
            offset=offset,
            limit=limit,
            total_count=len(records),
            has_more=offset + limit < len(records),
            partial=malformed > 0,
            warnings=["some configuration records were malformed"] if malformed else [],
        )

    async def get_skill(
        self,
        code: str,
        min_level: int,
        max_level: int | None,
        offset: int,
        limit: int,
        correlation_id: str | None = None,
    ) -> SkillProgressionResult:
        data, read, provenance = await self._read(CONFIG, "get_skill_progression", correlation_id)
        skills = as_mapping(data.get("skills"))
        if skills is None:
            raise app_errors.upstream_schema_changed(
                "skill configuration was unavailable", "get_skill_progression", procedure=CONFIG
            )
        raw = next((v for k, v in skills.items() if str(k).casefold() == code.casefold()), None)
        if raw is None:
            raise app_errors.not_found(
                "no configured skill matches this code", "get_skill_progression"
            )
        skill_record = as_mapping(raw)
        rows = as_mapping(skill_record.get("levels")) if skill_record is not None else None
        rows = rows or skill_record
        if rows is None:
            raise app_errors.upstream_schema_changed(
                "skill progression shape was not recognized",
                "get_skill_progression",
                procedure=CONFIG,
            )
        levels: list[dict[str, Any]] = []
        malformed = 0
        for key, value in rows.items():
            if not str(key).isdigit():
                continue
            level = int(key)
            if level < min_level or (max_level is not None and level > max_level):
                continue
            row = as_mapping(value)
            if row is None:
                malformed += 1
                continue
            normalized: dict[str, Any] = {"level": level}
            for source, dest, integral in (
                ("value", "value", False),
                ("cost", "cost", True),
                ("totalCost", "total_cost", True),
                ("unlockAtLevel", "unlock_at_player_level", True),
            ):
                if source in row:
                    n = finite_number(row[source], integral=integral)
                    if n is None:
                        malformed += 1
                    else:
                        normalized[dest] = n
            if "isABar" in row and isinstance(row["isABar"], bool):
                normalized["is_a_bar"] = row["isABar"]
            if len(normalized) == 1:
                malformed += 1
                continue
            levels.append(normalized)
        levels.sort(key=lambda x: x["level"])
        if not levels and malformed:
            raise app_errors.upstream_schema_changed(
                "skill progression contained no usable levels",
                "get_skill_progression",
                procedure=CONFIG,
            )
        page_levels = levels[offset : offset + limit]
        return SkillProgressionResult(
            observed_at=read.observed_at,
            provenance=provenance,
            skill_code=next(k for k in skills if str(k).casefold() == code.casefold()),
            levels=page_levels,
            offset=offset,
            limit=limit,
            total_count=len(levels),
            has_more=offset + limit < len(levels),
            partial=malformed > 0,
            warnings=["some skill levels were malformed"] if malformed else [],
        )

    async def get_item(self, code: str, correlation_id: str | None = None) -> ItemDetailsResult:
        data, read, provenance = await self._read(CONFIG, "get_item_details", correlation_id)
        items = as_mapping(data.get("items"))
        if items is None:
            raise app_errors.upstream_schema_changed(
                "item configuration was unavailable", "get_item_details", procedure=CONFIG
            )
        canonical = next((k for k in items if str(k).casefold() == code.casefold()), None)
        if canonical is None:
            raise app_errors.not_found("no configured item matches this code", "get_item_details")
        raw = as_mapping(items[canonical])
        if raw is None:
            raise app_errors.upstream_schema_changed(
                "item configuration shape was not recognized", "get_item_details", procedure=CONFIG
            )
        item: dict[str, Any] = {
            "item_code": canonical,
        }
        partial = False
        warnings: list[str] = []
        for key in (
            "type",
            "rarity",
            "usage",
            "isTradable",
            "isConsumable",
            "isDeposit",
            "climates",
        ):
            if key in raw and isinstance(raw[key], (str, bool)):
                public_key = {
                    "isTradable": "is_tradable",
                    "isConsumable": "is_consumable",
                    "isDeposit": "is_deposit",
                }.get(key, "item_type" if key == "type" else key)
                item[public_key] = raw[key]
            elif key == "climates" and isinstance(raw.get(key), list):
                climates = raw[key]
                if all(isinstance(climate, str) for climate in climates):
                    item["climates"] = climates
        effects: dict[str, int | float] = {}
        flat_stats = as_mapping(raw.get("flatStats"))
        if flat_stats is not None:
            for source, public, unit in (
                ("healthRegenPercent", "health_regen_fraction", "fraction"),
                ("percentAttack", "attack_bonus_fraction", "fraction"),
                ("buffDurationHours", "buff_duration_hours", "hours"),
                ("debuffDurationHours", "debuff_duration_hours", "hours"),
            ):
                if source not in flat_stats:
                    continue
                number = finite_number(flat_stats[source])
                if number is None:
                    partial = True
                    warnings.append("some item effects were malformed")
                    continue
                effects[public] = number / 100 if unit == "fraction" else number
        if effects:
            item["effects"] = effects
        dynamic_stats = as_mapping(raw.get("dynamicStats"))
        if dynamic_stats is not None:
            ranges: dict[str, dict[str, int | float]] = {}
            for stat, raw_range in dynamic_stats.items():
                if not isinstance(raw_range, list) or len(raw_range) != 2:
                    partial = True
                    warnings.append("some dynamic item stat ranges were malformed")
                    continue
                lower = finite_number(raw_range[0])
                upper = finite_number(raw_range[1])
                if lower is None or upper is None or lower > upper:
                    partial = True
                    warnings.append("some dynamic item stat ranges were malformed")
                    continue
                ranges[str(stat)] = {"minimum": lower, "maximum": upper}
            if ranges:
                item.setdefault("effects", {})["dynamic_stat_ranges"] = ranges
        if "dynamicStats" in raw:
            partial = True
            warnings.append("dynamic equipment stat ranges are not included in this projection")
        if "productionPoints" in raw:
            points = finite_number(raw["productionPoints"])
            needs = as_mapping(raw.get("productionNeeds"))
            if points is not None and points >= 0 and needs is not None:
                inputs = []
                valid = True
                for ingredient, quantity in needs.items():
                    amount = finite_number(quantity)
                    if ingredient not in items or amount is None or amount < 0:
                        valid = False
                        break
                    inputs.append({"item_code": ingredient, "quantity": amount})
                if valid:
                    item["production"] = {"production_points_per_unit": points, "inputs": inputs}
                else:
                    partial = True
                    warnings.append("recipe inputs were invalid or referenced unknown items")
            else:
                partial = True
                warnings.append("production recipe was incomplete or malformed")
        return ItemDetailsResult(
            observed_at=read.observed_at,
            provenance=provenance,
            item=item,
            partial=partial,
            warnings=warnings,
        )

    async def get_schedule(self, correlation_id: str | None = None) -> GameScheduleResult:
        data, read, provenance = await self._read(DATES, "get_game_schedule", correlation_id)
        schedule = {}
        warnings = []
        for key, public in SCHEDULE.items():
            value = data.get(key)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError
                    schedule[public] = parsed.astimezone(UTC)
                    if public.startswith("next_") and parsed.astimezone(UTC) < datetime.now(UTC):
                        warnings.append(f"{public} is already in the past")
                    continue
                except ValueError:
                    pass
            warnings.append(f"{public} was missing or invalid")
        if not schedule:
            raise app_errors.upstream_schema_changed(
                "schedule contained no usable timestamps", "get_game_schedule", procedure=DATES
            )
        return GameScheduleResult(
            observed_at=read.observed_at,
            provenance=provenance,
            schedule=schedule,
            warnings=warnings,
            partial=bool(warnings),
        )

    async def get_recipe(
        self, code: str, correlation_id: str | None = None
    ) -> tuple[Recipe | None, UpstreamRead]:
        data, read, provenance = await self._read(CONFIG, "get_company_overview", correlation_id)
        items = as_mapping(data.get("items"))
        item = as_mapping(items.get(code)) if items else None
        if item is None:
            return None, read
        points = finite_number(item.get("productionPoints"))
        needs = as_mapping(item.get("productionNeeds"))
        if points is None or points < 0 or needs is None:
            return None, read
        inputs = []
        for ingredient, raw_quantity in needs.items():
            qty = finite_number(raw_quantity)
            if not items or ingredient not in items or qty is None or qty < 0:
                return None, read
            inputs.append(RecipeInput(item_code=str(ingredient), quantity=qty))
        return Recipe(
            pp_per_unit=points,
            inputs=inputs,
            source="official_game_configuration",
            version=provenance.configuration_version,
            observed_at=read.observed_at,
            source_procedure=CONFIG,
        ), read
