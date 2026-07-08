from __future__ import annotations

from app.risk.risk_manager import RiskConfig


def make_risk_config(**overrides) -> RiskConfig:
    defaults: dict = {
        "seed_tickers": set(),
        "default_buy_allocation_pct": 1.0,
        "max_buy_allocation_pct": 10.0,
        "standard_buy_allocation_pct_max": 2.0,
        "reload_buy_allocation_pct_max": 5.0,
        "thesis_buy_allocation_pct_min": 3.0,
        "thesis_buy_allocation_pct_max": 7.0,
        "min_trade_notional_pct": 0.01,
        "min_trade_notional_usd": 1.0,
        "cash_buffer_pct": 0.0,
        "max_sell_notional_pct": 25.0,
        "simulation_portfolio_usd": 10_000.0,
        "new_ticker_size_multiplier": 10,
        "cooldown_seconds": 300,
        "duplicate_window_seconds": 300,
        "trading_window_enabled": False,
    }
    defaults.update(overrides)
    return RiskConfig(**defaults)
