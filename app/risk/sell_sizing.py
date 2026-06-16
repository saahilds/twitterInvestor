from __future__ import annotations

from app.execution.holdings import BrokerHolding


def holding_market_value_usd(holding: BrokerHolding) -> float | None:
    if holding.market_value is not None and holding.market_value > 0:
        return holding.market_value
    price = holding.last_price if holding.last_price is not None else holding.average_cost
    if price <= 0:
        return None
    return holding.quantity * price


def resolve_sell_notional_usd(
    holding: BrokerHolding,
    sell_fraction: float,
    *,
    max_trade_size_usd: float,
    min_trade_notional_usd: float = 1.0,
) -> float | None:
    """Dollar amount to sell as a fraction of the position's current market value."""
    fraction = min(1.0, max(0.0, sell_fraction))
    if fraction <= 0:
        return None

    market_value = holding_market_value_usd(holding)
    if market_value is None or market_value <= 0:
        return None

    target = market_value * fraction
    target = min(target, market_value, max_trade_size_usd)
    if target < min_trade_notional_usd:
        return None
    return round(target, 2)
