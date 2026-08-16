from __future__ import annotations

from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import BuyConviction


def resolve_portfolio_value(
    *,
    portfolio_value_usd: float | None,
    simulation_portfolio_usd: float,
) -> float:
    if portfolio_value_usd is not None and portfolio_value_usd > 0:
        return portfolio_value_usd
    return simulation_portfolio_usd


def interpolate_confidence(confidence: float) -> float:
    t = (confidence - 0.5) / 0.49
    return min(1.0, max(0.0, t))


def conviction_allocation_bounds(
    conviction: BuyConviction,
    *,
    default_buy_allocation_pct: float,
    standard_buy_allocation_pct_max: float,
    reload_buy_allocation_pct_max: float,
    thesis_buy_allocation_pct_min: float,
    thesis_buy_allocation_pct_max: float,
) -> tuple[float, float]:
    if conviction == BuyConviction.THESIS:
        return thesis_buy_allocation_pct_min, thesis_buy_allocation_pct_max
    if conviction == BuyConviction.RELOAD:
        return default_buy_allocation_pct, reload_buy_allocation_pct_max
    return default_buy_allocation_pct, standard_buy_allocation_pct_max


def resolve_buy_allocation_pct(
    signal: TradeSignal,
    conviction: BuyConviction,
    *,
    default_buy_allocation_pct: float,
    standard_buy_allocation_pct_max: float,
    reload_buy_allocation_pct_max: float,
    thesis_buy_allocation_pct_min: float,
    thesis_buy_allocation_pct_max: float,
) -> float:
    if signal.portfolio_allocation_pct is not None:
        return max(0.0, signal.portfolio_allocation_pct)

    pct_min, pct_max = conviction_allocation_bounds(
        conviction,
        default_buy_allocation_pct=default_buy_allocation_pct,
        standard_buy_allocation_pct_max=standard_buy_allocation_pct_max,
        reload_buy_allocation_pct_max=reload_buy_allocation_pct_max,
        thesis_buy_allocation_pct_min=thesis_buy_allocation_pct_min,
        thesis_buy_allocation_pct_max=thesis_buy_allocation_pct_max,
    )
    if pct_max <= pct_min:
        return pct_min
    t = interpolate_confidence(signal.confidence)
    return pct_min + t * (pct_max - pct_min)


def resolve_buy_notional_usd(
    *,
    portfolio_value_usd: float,
    allocation_pct: float,
    cash_available_usd: float | None,
    cash_buffer_pct: float,
    min_trade_notional_pct: float,
    min_trade_notional_usd: float,
    watchlist_multiplier: float = 1.0,
) -> float:
    target = portfolio_value_usd * allocation_pct / 100.0 * watchlist_multiplier

    cash_buffer = portfolio_value_usd * cash_buffer_pct / 100.0
    if cash_available_usd is not None:
        spendable = max(0.0, cash_available_usd - cash_buffer)
        target = min(target, spendable)

    floor = max(min_trade_notional_usd, portfolio_value_usd * min_trade_notional_pct / 100.0)
    return max(0.0, target) if target >= floor else 0.0


def resolve_max_sell_notional_usd(portfolio_value_usd: float, max_sell_notional_pct: float) -> float:
    return portfolio_value_usd * max_sell_notional_pct / 100.0


def resolve_min_trade_notional_usd(
    portfolio_value_usd: float,
    *,
    min_trade_notional_pct: float,
    min_trade_notional_usd: float,
) -> float:
    return max(min_trade_notional_usd, portfolio_value_usd * min_trade_notional_pct / 100.0)
