from datetime import datetime, timedelta, timezone

from app.models.db_models import SignalAction, Trade
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import BuyConviction
from app.risk.risk_manager import RiskConfig, RiskManager

AAOI_TWEET = """My pick for the full port challenge is...

Just took the position.


$AAOI


Here is the setup.


$AAOI
 is sitting roughly 30% off its all time highs and the reason is dilution overhang. Every time this company raises capital the stock sells off as the market digests new shares."""


def test_risk_reload_buy_uses_default_size(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"AAPL"},
            max_trade_size_usd=500,
            default_trade_size_usd=100,
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
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.RELOAD,
    )

    result = manager.evaluate(signal, db_session, cash_available_usd=2000.0)
    assert result.allowed
    assert result.is_new_ticker
    assert result.normalized_trade_usd == 100.0
    assert result.reason == "reload_sized"


def test_risk_thesis_buy_scales_with_confidence(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"AAPL"},
            max_trade_size_usd=2000,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            thesis_trade_min_usd=500,
            thesis_trade_max_usd=1000,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-adea",
        ticker="ADEA",
        action=SignalAction.BUY,
        confidence=0.72,
        raw_text="entered $ADEA at a 5% weight. here is the thesis.",
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.THESIS,
    )

    result = manager.evaluate(signal, db_session, cash_available_usd=2000.0)

    assert result.allowed
    assert result.is_new_ticker
    assert 500 <= result.normalized_trade_usd <= 1000
    assert result.reason.startswith("thesis_sized_")


def test_risk_thesis_buy_capped_by_cash(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=2000,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            thesis_trade_min_usd=500,
            thesis_trade_max_usd=1000,
            cash_buffer_usd=5,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-cap",
        ticker="AAOI",
        action=SignalAction.BUY,
        confidence=0.9,
        raw_text=AAOI_TWEET,
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.THESIS,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=300.0)

    assert result.allowed
    assert result.normalized_trade_usd == 295.0


def test_risk_reload_capped_by_low_cash(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=500,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-reload-cap",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding $NVDA starter",
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=50.0)

    assert result.allowed
    assert result.normalized_trade_usd == 50.0


def test_risk_blocks_live_buy_when_cash_unknown(db_session) -> None:
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"NVDA"},
            max_trade_size_usd=500,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
            live_trading_enabled=True,
        )
    )
    signal = TradeSignal(
        source_tweet_id="t-no-cash",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="adding NVDA",
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=None)

    assert not result.allowed
    assert result.reason == "insufficient_cash_data"


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
            max_trade_size_usd=500,
            default_trade_size_usd=100,
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
            amount_usd=100,
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
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(signal, db_session)

    assert not result.allowed
    assert result.reason.startswith("cooldown_active")
