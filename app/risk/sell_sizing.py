from __future__ import annotations

import math
from dataclasses import dataclass

from app.execution.holdings import BrokerHolding


@dataclass(slots=True)
class SellOrderSizing:
    amount_usd: float
    quantity: float


def holding_market_value_usd(holding: BrokerHolding) -> float | None:
    if holding.market_value is not None and holding.market_value > 0:
        return holding.market_value
    price = holding.last_price if holding.last_price is not None else holding.average_cost
    if price <= 0:
        return None
    return holding.quantity * price


def sell_fraction_to_target_weight(
    *,
    holding_market_value: float,
    portfolio_value_usd: float,
    target_portfolio_pct: float,
) -> float | None:
    """Return fraction of position to sell to reach target portfolio weight %.

    Returns ``None`` when already at/below target or inputs are invalid.
    """
    if portfolio_value_usd <= 0 or holding_market_value <= 0:
        return None
    target = max(0.0, min(100.0, target_portfolio_pct))
    current_weight_pct = 100.0 * holding_market_value / portfolio_value_usd
    if current_weight_pct <= target:
        return None
    return (current_weight_pct - target) / current_weight_pct


def _round_quantity(quantity: float) -> float:
    return math.floor(max(0.0, quantity) * 1_000_000) / 1_000_000


def resolve_sell_order(
    holding: BrokerHolding,
    sell_fraction: float,
    *,
    max_trade_size_usd: float,
    min_trade_notional_usd: float = 1.0,
) -> SellOrderSizing | None:
    """Size a sell from the live broker-reported position quantity and price."""
    fraction = min(1.0, max(0.0, sell_fraction))
    if fraction <= 0 or holding.quantity <= 0:
        return None

    market_value = holding_market_value_usd(holding)
    if market_value is None or market_value <= 0:
        return None

    price = holding.last_price if holding.last_price is not None else holding.average_cost
    if price <= 0:
        return None

    # Quantity is derived from the broker-reported holding, so it can never
    # exceed the shares currently owned.
    target_qty = holding.quantity * fraction
    target_qty = _round_quantity(target_qty)
    if target_qty <= 0:
        return None

    target_amount = target_qty * price

    # Partial trims respect max trade size; full exits use the whole sellable position.
    if fraction < 0.999:
        target_amount = min(target_amount, market_value * fraction, max_trade_size_usd)
        target_qty = _round_quantity(min(target_qty, target_amount / price))

    target_amount = min(target_amount, market_value)
    if target_amount < min_trade_notional_usd or target_qty <= 0:
        return None

    return SellOrderSizing(
        amount_usd=round(target_amount, 2),
        quantity=target_qty,
    )


def resolve_sell_notional_usd(
    holding: BrokerHolding,
    sell_fraction: float,
    *,
    max_trade_size_usd: float,
    min_trade_notional_usd: float = 1.0,
) -> float | None:
    """Backward-compatible notional-only helper."""
    sizing = resolve_sell_order(
        holding,
        sell_fraction,
        max_trade_size_usd=max_trade_size_usd,
        min_trade_notional_usd=min_trade_notional_usd,
    )
    return sizing.amount_usd if sizing is not None else None
