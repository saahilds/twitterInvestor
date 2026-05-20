from datetime import datetime, timedelta, timezone

from app.models.db_models import SignalAction, Trade
from app.models.schemas import TradeSignal
from app.risk.risk_manager import RiskConfig, RiskManager


def test_risk_rejects_unapproved_ticker(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            allowlist={"AAPL"},
            max_trade_size_usd=5,
            default_trade_size_usd=1,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-1",
        ticker="TSLA",
        action=SignalAction.BUY,
        raw_text="adding TSLA",
        suggested_trade_usd=1,
    )

    result = manager.evaluate(signal, db_session)
    assert not result.allowed
    assert result.reason.startswith("ticker_not_allowed")


def test_risk_enforces_cooldown(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            allowlist={"NVDA"},
            max_trade_size_usd=5,
            default_trade_size_usd=1,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
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
