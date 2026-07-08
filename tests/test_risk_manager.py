from datetime import datetime, timedelta, timezone

import pytest

from app.models.db_models import SignalAction, Trade
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import BuyConviction
from app.risk.risk_manager import RiskManager
from app.testing.risk_config import make_risk_config

AAOI_TWEET = """My pick for the full port challenge is...

Just took the position.


$AAOI


Here is the setup.


$AAOI
 is sitting roughly 30% off its all time highs and the reason is dilution overhang. Every time this company raises capital the stock sells off as the market digests new shares."""

PORTFOLIO = 10_000.0


def test_risk_reload_buy_scales_with_confidence(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers={"AAPL"}))
    signal = TradeSignal(
        source_tweet_id="t-1",
        ticker="TSLA",
        action=SignalAction.BUY,
        confidence=0.81,
        raw_text="adding TSLA",
        buy_conviction=BuyConviction.RELOAD,
    )

    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        cash_available_usd=PORTFOLIO,
        portfolio_value_usd=PORTFOLIO,
    )
    assert result.allowed
    assert result.is_new_ticker
    assert result.normalized_trade_usd > 100.0
    assert result.normalized_trade_usd == pytest.approx(353.0, abs=1.0)
    assert result.reason == "reload_sized"


def test_risk_thesis_buy_uses_tweet_allocation_pct(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers={"AAPL"}))
    signal = TradeSignal(
        source_tweet_id="t-adea",
        ticker="ADEA",
        action=SignalAction.BUY,
        confidence=0.72,
        raw_text="entered $ADEA at a 5% weight. here is the thesis.",
        buy_conviction=BuyConviction.THESIS,
        portfolio_allocation_pct=5.0,
    )

    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        cash_available_usd=PORTFOLIO,
        portfolio_value_usd=PORTFOLIO,
    )

    assert result.allowed
    assert result.normalized_trade_usd == 500.0
    assert result.reason == "allocation_5pct_500"


def test_risk_thesis_buy_scales_with_confidence_without_tweet_pct(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers={"AAPL"}))
    signal = TradeSignal(
        source_tweet_id="t-thesis",
        ticker="ADEA",
        action=SignalAction.BUY,
        confidence=0.72,
        raw_text="new position for the subs. here is the thesis on $ADEA",
        buy_conviction=BuyConviction.THESIS,
    )

    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        cash_available_usd=PORTFOLIO,
        portfolio_value_usd=PORTFOLIO,
    )

    assert result.allowed
    assert 470.0 <= result.normalized_trade_usd <= 490.0


def test_risk_thesis_buy_capped_by_cash(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers=set(), cash_buffer_pct=0.5))
    signal = TradeSignal(
        source_tweet_id="t-cap",
        ticker="AAOI",
        action=SignalAction.BUY,
        confidence=0.9,
        raw_text=AAOI_TWEET,
        buy_conviction=BuyConviction.THESIS,
        portfolio_allocation_pct=7.0,
    )
    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        cash_available_usd=300.0,
        portfolio_value_usd=PORTFOLIO,
    )

    assert result.allowed
    assert result.normalized_trade_usd == 250.0


def test_risk_reload_capped_by_low_cash(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers=set()))
    signal = TradeSignal(
        source_tweet_id="t-reload-cap",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding $NVDA starter",
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        cash_available_usd=50.0,
        portfolio_value_usd=PORTFOLIO,
    )

    assert result.allowed
    assert result.normalized_trade_usd == 50.0


def test_risk_blocks_live_buy_when_cash_unknown(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers={"NVDA"}, live_trading_enabled=True))
    signal = TradeSignal(
        source_tweet_id="t-no-cash",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding NVDA",
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(signal, db_session, manager_id="individual", cash_available_usd=None)

    assert not result.allowed
    assert result.reason == "insufficient_cash_data"


def test_risk_blocks_unlisted_buy_when_confidence_below_floor(db_session) -> None:
    manager = RiskManager(
        make_risk_config(seed_tickers={"AAPL"}, min_buy_confidence_unlisted=0.5)
    )
    signal = TradeSignal(
        source_tweet_id="t-weak",
        ticker="ADEA",
        action=SignalAction.BUY,
        confidence=0.2,
        raw_text="maybe watching $ADEA",
    )

    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        cash_available_usd=500.0,
        portfolio_value_usd=PORTFOLIO,
    )

    assert not result.allowed
    assert result.reason == "unlisted_buy_low_confidence"


def test_risk_enforces_cooldown(db_session) -> None:
    manager = RiskManager(make_risk_config(seed_tickers={"NVDA"}))
    db_session.add(
        Trade(
            parsed_signal_id=1,
            ticker="NVDA",
            action=SignalAction.BUY,
            amount_usd=100,
            quantity=None,
            status="simulated",
            simulation=True,
            broker_order_id="x",
            response_json="{}",
            manager_id="individual",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
        )
    )
    db_session.commit()

    signal = TradeSignal(
        source_tweet_id="t-2",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding NVDA",
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        portfolio_value_usd=PORTFOLIO,
    )

    assert not result.allowed
    assert result.reason.startswith("cooldown_active")
