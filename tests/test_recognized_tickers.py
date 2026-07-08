from app.models.db_models import RecognizedTicker, SignalAction
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import BuyConviction
from app.risk.risk_manager import RiskManager
from app.services.recognized_tickers import RecognizedTickerRegistry
from app.testing.risk_config import make_risk_config

PORTFOLIO = 10_000.0


def test_thesis_new_ticker_buy_sized_by_conviction(db_session) -> None:
    registry = RecognizedTickerRegistry()
    manager = RiskManager(make_risk_config(seed_tickers=set()), registry=registry)
    signal = TradeSignal(
        source_tweet_id="t-new",
        ticker="AAOI",
        action=SignalAction.BUY,
        confidence=0.93,
        raw_text="took the position $AAOI. here is the setup. long thesis body.",
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
    assert result.is_new_ticker
    assert 650.0 <= result.normalized_trade_usd <= 700.0
    assert result.reason.startswith("thesis_sized_")


def test_thesis_buy_capped_by_cash(db_session) -> None:
    registry = RecognizedTickerRegistry()
    manager = RiskManager(make_risk_config(seed_tickers=set()), registry=registry)
    signal = TradeSignal(
        source_tweet_id="t-cap",
        ticker="AAOI",
        action=SignalAction.BUY,
        confidence=0.9,
        raw_text="took the position $AAOI. here is the thesis.",
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
    assert result.normalized_trade_usd == 300.0


def test_recognized_ticker_reload_uses_default_allocation(db_session) -> None:
    registry = RecognizedTickerRegistry()
    registry.register("AAOI", db_session, manager_id="individual", source_tweet_id="seed")
    manager = RiskManager(make_risk_config(seed_tickers=set()), registry=registry)
    signal = TradeSignal(
        source_tweet_id="t-known",
        ticker="AAOI",
        action=SignalAction.BUY,
        raw_text="adding $AAOI",
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
    assert not result.is_new_ticker
    assert result.normalized_trade_usd == 100.0
    assert result.reason == "reload_sized"


def test_registry_register_idempotent(db_session) -> None:
    from sqlalchemy import select

    registry = RecognizedTickerRegistry()
    registry.register("NVDA", db_session, manager_id="individual", source_tweet_id="1")
    registry.register("NVDA", db_session, manager_id="individual", source_tweet_id="2")
    rows = db_session.execute(select(RecognizedTicker)).scalars().all()
    assert len(rows) == 1
