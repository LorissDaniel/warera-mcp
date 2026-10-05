"""JWT-only player inventory and owner-order aggregates, with no shared caching."""

from __future__ import annotations

import asyncio

from warera_mcp import errors as app_errors
from warera_mcp.application.common import UpstreamCaller, composite_observed_at
from warera_mcp.application.players import PlayerResolver, require_player_identifier
from warera_mcp.auth.credentials import PlayerRequestContext
from warera_mcp.domain.game_configuration_normalization import finite_number
from warera_mcp.domain.item_catalog import WARERA_ITEM_CODES
from warera_mcp.domain.models import PlayerRef, PlayerResourcesResult
from warera_mcp.warera.client import UpstreamRead
from warera_mcp.warera.schemas import as_mapping, extract_ident, safe_ident

INVENTORY = "inventory.getById"
ORDERS = "tradingOrder.getAllOrdersByOwner"


def quantity_map(value: object, item_codes: list[str] | None) -> dict[str, float] | None:
    """Absent codes in a valid sparse quantity map are zero; invalid maps never are."""
    mapping = as_mapping(value)
    if mapping is None:
        return None
    result: dict[str, float] = {}
    for code, value in mapping.items():
        number = finite_number(value)
        if safe_ident(code) is None or number is None or number < 0:
            return None
        result[code] = float(number)
    return {code: result.get(code, 0.0) for code in item_codes} if item_codes else result


def nonnegative(value: object) -> float | None:
    number = finite_number(value)
    return float(number) if number is not None and number >= 0 else None


class ResourceService:
    def __init__(self, caller: UpstreamCaller, players: PlayerResolver) -> None:
        self._caller = caller
        self._players = players

    async def get_player_resources(
        self,
        *,
        user_id: str | None,
        username: str | None,
        item_codes: list[str] | None,
        include_orders: bool,
        credentials: PlayerRequestContext | None = None,
        correlation_id: str | None = None,
    ) -> PlayerResourcesResult:
        operation = "get_player_resources"
        require_player_identifier(user_id, username, operation)
        if item_codes is not None and any(code not in WARERA_ITEM_CODES for code in item_codes):
            raise app_errors.invalid_input("unknown item_code; use get_item_catalog", operation)
        # Fail before public profile lookups if the strictly necessary credential is absent.
        if credentials is None or credentials.jwt is None:
            from warera_mcp.application.common import map_upstream_error
            from warera_mcp.auth.requirements import CredentialKind
            from warera_mcp.warera.errors import WareraMissingCredential

            raise map_upstream_error(
                WareraMissingCredential(required=(CredentialKind.JWT,)),
                operation=operation,
                procedure=INVENTORY,
                credentials=credentials,
            )
        resolved = await self._players.resolve(
            user_id=user_id,
            username=username,
            credentials=None,
            correlation_id=correlation_id,
            operation=operation,
        )
        params = {"userId": resolved.profile.id}
        if include_orders:
            inventory, orders = await asyncio.gather(
                self._caller.try_read(
                    operation,
                    INVENTORY,
                    params,
                    credentials=credentials,
                    correlation_id=correlation_id,
                ),
                self._caller.try_read(
                    operation,
                    ORDERS,
                    params,
                    credentials=credentials,
                    correlation_id=correlation_id,
                ),
            )
        else:
            inventory = await self._caller.read(
                operation, INVENTORY, params, credentials=credentials, correlation_id=correlation_id
            )
            orders = None
        if isinstance(inventory, app_errors.AppError):
            raise inventory
        row = as_mapping(inventory.data)
        inventory_id = extract_ident(row) if row is not None else None
        if (
            row is None
            or inventory_id is None
            or extract_ident(row.get("user")) != resolved.profile.id
        ):
            raise app_errors.upstream_schema_changed(
                "inventory identity or owner did not match the requested player",
                operation,
                procedure=INVENTORY,
            )
        items = as_mapping(row.get("items")) or {}
        market = as_mapping(row.get("market")) or {}
        available = quantity_map(items.get("basics"), item_codes)
        reserved = quantity_map(market.get("basics"), item_codes)
        money = nonnegative(row.get("money"))
        locked = nonnegative(market.get("lockedMoney"))
        warnings = list(resolved.warnings)
        partial = any(value is None for value in (available, reserved, money, locked))
        if partial:
            warnings.append("some inventory amounts were missing or malformed; unknown is not zero")
        reads = [inventory]
        sell_quantities = None
        buy_money = None
        sell_money = None
        orders_observed_at = None
        if isinstance(orders, UpstreamRead):
            reads.append(orders)
            orders_observed_at = orders.observed_at
            order_row = as_mapping(orders.data) or {}
            sell_quantities = quantity_map(order_row.get("totalSellQuantities"), item_codes)
            buy_money = nonnegative(order_row.get("totalBuyMoneyInvested"))
            sell_money = nonnegative(order_row.get("totalSellMoneyExpected"))
            if any(value is None for value in (sell_quantities, buy_money, sell_money)):
                partial = True
                warnings.append("some owner-order aggregates were missing or malformed")
        elif include_orders:
            partial = True
            code = orders.code.value if isinstance(orders, app_errors.AppError) else "UNAVAILABLE"
            warnings.append(
                f"owner orders were unavailable ({code}); selling quantities are unknown"
            )
        return PlayerResourcesResult(
            observed_at=composite_observed_at(reads),
            warnings=warnings,
            player=PlayerRef(id=resolved.profile.id, username=resolved.profile.username),
            inventory_id=inventory_id,
            money_available=money,
            items_available=available,
            money_reserved=locked,
            items_reserved_for_market=reserved,
            sell_quantities=sell_quantities,
            money_in_buy_orders=buy_money,
            expected_sales_proceeds=sell_money,
            orders_observed_at=orders_observed_at,
            orders_requested=include_orders,
            partial=partial,
        )
