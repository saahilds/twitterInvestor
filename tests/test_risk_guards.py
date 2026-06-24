from datetime import datetime, timezone

from app.execution.holdings import BrokerHolding
from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.models.schemas import TradeSignal
from app.risk.risk_manager import RiskConfig, RiskManager


def _manager(**overrides) -> RiskManager:
    defaults = {
        "seed_tickers": {"NVDA", "AAPL"},
        "max_trade_size_usd": 5,
        "default_trade_size_usd": 1,
        "new_ticker_size_multiplier": 10,
        "cooldown_seconds": 300,
        "duplicate_window_seconds": 300,
        "trading_window_enabled": False,
        "us_symbols_only": True,
        "max_trades_per_ticker_per_day": 1,
        "daily_limit_counts_simulation": True,
    }
    defaults.update(overrides)
    return RiskManager(RiskConfig(**defaults))


def test_risk_rejects_sell_when_not_in_portfolio(db_session) -> None:
    manager = _manager(live_trading_enabled=True)
    signal = TradeSignal(
        source_tweet_id="t-sell",
        ticker="NVDA",
        action=SignalAction.SELL,
        raw_text="selling NVDA",
        sell_fraction=1.0,
    )
    result = manager.evaluate(signal, db_session, holding=None, manager_id="individual")
    assert not result.allowed
    assert result.reason == "not_in_portfolio:NVDA"


def test_risk_sells_fraction_of_portfolio_holding(db_session) -> None:
    manager = _manager(live_trading_enabled=True, max_trade_size_usd=10_000)
    holding = BrokerHolding(
        ticker="ADEA",
        quantity=20.0,
        average_cost=10.0,
        last_price=50.0,
        market_value=1000.0,
        cost_basis=200.0,
        unrealized_pnl=800.0,
        unrealized_pnl_pct=400.0,
    )
    signal = TradeSignal(
        source_tweet_id="t-sell-half",
        ticker="ADEA",
        action=SignalAction.SELL,
        raw_text="sold half my $ADEA",
        sell_fraction=0.5,
    )
    result = manager.evaluate(signal, db_session, holding=holding, manager_id="individual")
    assert result.allowed
    assert result.normalized_trade_usd == 497.5
    assert result.sell_fraction == 0.5
    assert result.sell_quantity == 9.95
    assert result.reason == "sell_50pct_portfolio"


def test_risk_rejects_non_us_symbol(db_session) -> None:
    manager = _manager()
    signal = TradeSignal(
        source_tweet_id="t-ax",
        ticker="EOS.AX",
        action=SignalAction.BUY,
        raw_text="adding EOS.AX",
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session, manager_id="individual")
    assert not result.allowed
    assert result.reason.startswith("non_us_symbol")


def test_risk_rejects_duplicate_tweet(db_session) -> None:
    manager = _manager()
    parsed = ParsedSignal(
        tweet_pk=1,
        source_tweet_id="tweet-100",
        ticker="NVDA",
        action=SignalAction.BUY,
        confidence=0.8,
        strength="medium",
        score=3,
        raw_text="adding NVDA",
        manager_id="individual",
        suggested_trade_usd=1,
    )
    db_session.add(parsed)
    db_session.flush()
    db_session.add(
        Trade(
            parsed_signal_id=parsed.id,
            ticker="NVDA",
            action=SignalAction.BUY,
            amount_usd=1,
            quantity=0.01,
            status="simulated",
            simulation=True,
            broker_order_id="x",
            response_json="{}",
            manager_id="individual",
        )
    )
    db_session.commit()

    signal = TradeSignal(
        source_tweet_id="tweet-100",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding NVDA again",
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session, manager_id="individual")
    assert not result.allowed
    assert result.reason.startswith("duplicate_tweet")


def test_risk_daily_limit_per_ticker(db_session) -> None:
    manager = _manager(cooldown_seconds=0, duplicate_window_seconds=0)
    parsed = ParsedSignal(
        tweet_pk=1,
        source_tweet_id="tweet-1",
        ticker="NVDA",
        action=SignalAction.BUY,
        confidence=0.8,
        strength="medium",
        score=3,
        raw_text="adding NVDA",
        manager_id="individual",
        suggested_trade_usd=1,
    )
    db_session.add(parsed)
    db_session.flush()
    db_session.add(
        Trade(
            parsed_signal_id=parsed.id,
            ticker="NVDA",
            action=SignalAction.BUY,
            amount_usd=1,
            quantity=0.01,
            status="simulated",
            simulation=True,
            broker_order_id="x",
            response_json="{}",
            manager_id="individual",
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    signal = TradeSignal(
        source_tweet_id="tweet-2",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding NVDA",
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session, manager_id="individual")
    assert not result.allowed
    assert result.reason.startswith("daily_limit")
