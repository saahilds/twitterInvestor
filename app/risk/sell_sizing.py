from __future__ import annotations

import math
from dataclasses import dataclass

from app.execution.holdings import BrokerHolding

# Leave headroom for price drift and Robinhood rounding on full exits.
_SELL_BUFFER = 0.995


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

    target_qty = holding.quantity * fraction * _SELL_BUFFER
    target_qty = _round_quantity(target_qty)
    if target_qty <= 0:
        return None

    target_amount = target_qty * price

    # Partial trims respect max trade size; full exits use the whole sellable position.
    if fraction < 0.999:
        target_amount = min(target_amount, market_value * fraction, max_trade_size_usd)
        target_qty = _round_quantity(min(target_qty, target_amount / price))

    target_amount = min(target_amount, market_value * _SELL_BUFFER)
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
