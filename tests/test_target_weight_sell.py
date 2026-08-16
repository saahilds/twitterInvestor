import pytest

from app.execution.holdings import BrokerHolding
from app.models.db_models import SignalAction
from app.models.schemas import TradeSignal
from app.risk.risk_manager import RiskManager
from app.risk.sell_sizing import sell_fraction_to_target_weight
from app.parsing.watch_conviction import WatchConviction
from app.services.watchlist import WatchlistRegistry
from app.testing.risk_config import make_risk_config


def _holding(*, market_value: float) -> BrokerHolding:
    quantity = market_value / 50.0
    return BrokerHolding(
        ticker="VPG",
        quantity=quantity,
        average_cost=50.0,
        last_price=50.0,
        market_value=market_value,
        cost_basis=market_value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def test_sell_fraction_to_target_weight_math() -> None:
    # 5% of a $10k sleeve = $500; target 2.1% → sell (5-2.1)/5
    frac = sell_fraction_to_target_weight(
        holding_market_value=500.0,
        portfolio_value_usd=10_000.0,
        target_portfolio_pct=2.1,
    )
    assert frac == pytest.approx((5.0 - 2.1) / 5.0)


def test_sell_fraction_already_at_target() -> None:
    assert (
        sell_fraction_to_target_weight(
            holding_market_value=200.0,
            portfolio_value_usd=10_000.0,
            target_portfolio_pct=2.1,
        )
        is None
    )


def test_risk_sell_to_target_weight(db_session) -> None:
    manager = RiskManager(make_risk_config(ck_portfolio_usd=10_000.0, trading_window_enabled=False))
    signal = TradeSignal(
        source_tweet_id="t1",
        ticker="VPG",
        action=SignalAction.SELL,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text="Trimmed $VPG down to 2.1%",
        target_portfolio_pct=2.1,
    )
    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        portfolio_value_usd=10_000.0,
        holding=_holding(market_value=500.0),
    )
    assert result.allowed is True
    assert result.sell_fraction == pytest.approx((5.0 - 2.1) / 5.0)


def test_risk_reject_when_already_at_target(db_session) -> None:
    manager = RiskManager(make_risk_config(ck_portfolio_usd=10_000.0, trading_window_enabled=False))
    signal = TradeSignal(
        source_tweet_id="t2",
        ticker="VPG",
        action=SignalAction.SELL,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text="Trimmed $VPG down to 2.1%",
        target_portfolio_pct=2.1,
    )
    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        portfolio_value_usd=10_000.0,
        holding=_holding(market_value=200.0),  # 2% of 10k
    )
    assert result.allowed is False
    assert "already_at_or_below_target" in (result.reason or "")


def test_target_weight_uses_ck_portfolio_notional(db_session) -> None:
    """Weight is vs CK sleeve ($10k), not a larger RH equity figure."""
    manager = RiskManager(make_risk_config(ck_portfolio_usd=10_000.0, trading_window_enabled=False))
    # $500 holding is 5% of CK $10k sleeve; if someone wrongly used $50k RH equity it'd be 1%.
    signal = TradeSignal(
        source_tweet_id="t3",
        ticker="VPG",
        action=SignalAction.SELL,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text="Trimmed $VPG down to 2.1%",
        target_portfolio_pct=2.1,
    )
    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        portfolio_value_usd=10_000.0,  # CK sleeve passed by AccountManager
        holding=_holding(market_value=500.0),
    )
    assert result.allowed is True
    assert result.sell_fraction == pytest.approx((5.0 - 2.1) / 5.0)
    # Not the fraction you'd get if weight were $500/$50k = 1% (already below 2.1 → reject)
    assert result.sell_fraction > 0.5


def test_target_weight_sell_ignores_watchlist_and_fallback_cap(db_session) -> None:
    watchlist = WatchlistRegistry(max_conviction_score=5.0, stale_days=30)
    watchlist.upsert(
        "VPG",
        db_session,
        manager_id="individual",
        watch_conviction=WatchConviction.HEAVY,
    )
    manager = RiskManager(
        make_risk_config(
            ck_portfolio_usd=10_000.0,
            max_sell_notional_pct=1.0,
            trading_window_enabled=False,
        ),
        watchlist=watchlist,
    )
    signal = TradeSignal(
        source_tweet_id="t4",
        ticker="VPG",
        action=SignalAction.SELL,
        confidence=0.9,
        raw_text="Trimmed $VPG down to 2.1%",
        target_portfolio_pct=2.1,
        sell_sizing_explicit=True,
    )

    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        portfolio_value_usd=10_000.0,
        holding=_holding(market_value=500.0),
    )

    assert result.allowed is True
    assert result.sell_fraction == pytest.approx(0.58)
    assert result.normalized_trade_usd == 290.0
    assert result.sell_quantity == pytest.approx(5.8)


def test_explicit_full_sell_is_capped_at_shares_owned(db_session) -> None:
    manager = RiskManager(
        make_risk_config(max_sell_notional_pct=1.0, trading_window_enabled=False)
    )
    signal = TradeSignal(
        source_tweet_id="t5",
        ticker="VPG",
        action=SignalAction.SELL,
        confidence=0.9,
        raw_text="Closed $VPG",
        sell_fraction=1.0,
        sell_sizing_explicit=True,
    )

    result = manager.evaluate(
        signal,
        db_session,
        manager_id="individual",
        portfolio_value_usd=5_000.0,
        holding=_holding(market_value=250.0),
    )

    assert result.allowed is True
    assert result.normalized_trade_usd == 250.0
    assert result.sell_quantity == 5.0
