from datetime import datetime, timedelta, timezone

from app.models.db_models import SignalAction, Trade
from app.models.schemas import TradeSignal
from app.risk.risk_manager import RiskConfig, RiskManager


def test_risk_allows_new_us_ticker_buy(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"AAPL"},
            max_trade_size_usd=5,
            default_trade_size_usd=1,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-1",
        ticker="TSLA",
        action=SignalAction.BUY,
        raw_text="adding TSLA",
        suggested_trade_usd=1,
    )

    result = manager.evaluate(signal, db_session, cash_available_usd=50.0)
    assert result.allowed
    assert result.is_new_ticker
    assert result.normalized_trade_usd == 10.0


def test_risk_allows_confident_buy_when_ticker_not_in_seed(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"AAPL"},
            max_trade_size_usd=500,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
            min_buy_confidence_unlisted=0.5,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-adea",
        ticker="ADEA",
        action=SignalAction.BUY,
        confidence=0.72,
        raw_text="entered $ADEA",
        suggested_trade_usd=100,
    )

    result = manager.evaluate(signal, db_session, cash_available_usd=500.0)

    assert result.allowed
    assert result.is_new_ticker
    assert result.normalized_trade_usd == 500.0


def test_risk_blocks_unlisted_buy_when_confidence_below_floor(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"AAPL"},
            max_trade_size_usd=500,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
            min_buy_confidence_unlisted=0.5,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-weak",
        ticker="ADEA",
        action=SignalAction.BUY,
        confidence=0.2,
        raw_text="maybe watching $ADEA",
        suggested_trade_usd=100,
    )

    result = manager.evaluate(signal, db_session, cash_available_usd=500.0)

    assert not result.allowed
    assert result.reason == "unlisted_buy_low_confidence"


def test_risk_enforces_cooldown(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"NVDA"},
            new_ticker_size_multiplier=10,
            max_trade_size_usd=5,
            default_trade_size_usd=1,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        )
    )
    db_session.add(
        Trade(
            parsed_signal_id=1,
            ticker="NVDA",
            action=SignalAction.BUY,
            amount_usd=1,
            quantity=None,
            status="simulated",
            simulation=True,
            broker_order_id="x",
            response_json="{}",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
    )
    db_session.commit()

    signal = TradeSignal(
        source_tweet_id="t-2",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding NVDA",
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session)

    assert not result.allowed
    assert result.reason.startswith("cooldown_active")
