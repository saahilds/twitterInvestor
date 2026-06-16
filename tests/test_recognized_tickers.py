from app.models.db_models import RecognizedTicker, SignalAction
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import BuyConviction
from app.risk.risk_manager import RiskConfig, RiskManager
from app.services.recognized_tickers import RecognizedTickerRegistry


def test_thesis_new_ticker_buy_sized_by_conviction(db_session) -> None:
    registry = RecognizedTickerRegistry()
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=2000,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            thesis_trade_min_usd=500,
            thesis_trade_max_usd=1000,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        ),
        registry=registry,
    )
    signal = TradeSignal(
        source_tweet_id="t-new",
        ticker="AAOI",
        action=SignalAction.BUY,
        confidence=0.93,
        raw_text="took the position $AAOI. here is the setup. long thesis body.",
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.THESIS,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=2000.0)

    assert result.allowed
    assert result.is_new_ticker
    assert 500 <= result.normalized_trade_usd <= 1000
    assert result.reason.startswith("thesis_sized_")


def test_thesis_buy_capped_by_cash(db_session) -> None:
    registry = RecognizedTickerRegistry()
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=2000,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            thesis_trade_min_usd=500,
            thesis_trade_max_usd=1000,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        ),
        registry=registry,
    )
    signal = TradeSignal(
        source_tweet_id="t-cap",
        ticker="AAOI",
        action=SignalAction.BUY,
        confidence=0.9,
        raw_text="took the position $AAOI. here is the thesis.",
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.THESIS,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=300.0)

    assert result.allowed
    assert result.normalized_trade_usd == 300.0


def test_recognized_ticker_reload_uses_default_size(db_session) -> None:
    registry = RecognizedTickerRegistry()
    registry.register("AAOI", db_session, source_tweet_id="seed")
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=500,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        ),
        registry=registry,
    )
    signal = TradeSignal(
        source_tweet_id="t-known",
        ticker="AAOI",
        action=SignalAction.BUY,
        raw_text="adding $AAOI",
        suggested_trade_usd=100,
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=500.0)

    assert result.allowed
    assert not result.is_new_ticker
    assert result.normalized_trade_usd == 100.0
    assert result.reason == "reload_sized"


def test_registry_register_idempotent(db_session) -> None:
    from sqlalchemy import select

    registry = RecognizedTickerRegistry()
    registry.register("NVDA", db_session, source_tweet_id="1")
    registry.register("NVDA", db_session, source_tweet_id="2")
    rows = db_session.execute(select(RecognizedTicker)).scalars().all()
    assert len(rows) == 1
