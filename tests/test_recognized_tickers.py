from app.models.db_models import RecognizedTicker, SignalAction
from app.models.schemas import TradeSignal
from app.risk.risk_manager import RiskConfig, RiskManager
from app.services.recognized_tickers import RecognizedTickerRegistry


def test_new_ticker_buy_is_sized_up(db_session) -> None:
    registry = RecognizedTickerRegistry()
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=5,
            default_trade_size_usd=1,
            new_ticker_size_multiplier=10,
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
        raw_text="took the position $AAOI",
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=100.0)

    assert result.allowed
    assert result.is_new_ticker
    assert result.normalized_trade_usd == 10.0
    assert result.reason == "new_ticker_sized_up"


def test_new_ticker_buy_capped_by_cash(db_session) -> None:
    registry = RecognizedTickerRegistry()
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=5,
            default_trade_size_usd=1,
            new_ticker_size_multiplier=10,
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
        raw_text="buy $AAOI",
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=4.5)

    assert result.allowed
    assert result.normalized_trade_usd == 4.5


def test_recognized_ticker_uses_default_size(db_session) -> None:
    registry = RecognizedTickerRegistry()
    registry.register("AAOI", db_session, source_tweet_id="seed")
    manager = RiskManager(
        RiskConfig(
            seed_tickers=set(),
            max_trade_size_usd=5,
            default_trade_size_usd=1,
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
        suggested_trade_usd=1,
    )
    result = manager.evaluate(signal, db_session, cash_available_usd=100.0)

    assert result.allowed
    assert not result.is_new_ticker
    assert result.normalized_trade_usd == 1.0


def test_registry_register_idempotent(db_session) -> None:
    from sqlalchemy import select

    registry = RecognizedTickerRegistry()
    registry.register("NVDA", db_session, source_tweet_id="1")
    registry.register("NVDA", db_session, source_tweet_id="2")
    rows = db_session.execute(select(RecognizedTicker)).scalars().all()
    assert len(rows) == 1
