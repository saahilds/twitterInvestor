from datetime import datetime, timedelta, timezone

from app.models.db_models import SignalAction, WatchlistEntry
from app.parsing.watch_conviction import WatchConviction, infer_watch_conviction, watch_size_multiplier
from app.risk.risk_manager import RiskConfig, RiskManager
from app.services.watchlist import WatchlistRegistry


def test_infer_heavy_watch_from_valuation_call() -> None:
    text = "$NOK is a $20 stock trading at $14"
    assert infer_watch_conviction(text) == WatchConviction.HEAVY


def test_infer_soft_watch_from_deploy_list() -> None:
    text = "if i did deploy some cash anywhere personally it would be in these stocks $CRDO $NBIS"
    assert infer_watch_conviction(text) == WatchConviction.SOFT


def test_infer_start_watch() -> None:
    text = "$NOK is starting to look very interesting to enter back in"
    assert infer_watch_conviction(text) == WatchConviction.START


def test_watchlist_upsert_accumulates_conviction(db_session) -> None:
    registry = WatchlistRegistry(max_conviction_score=5.0, stale_days=30)
    registry.upsert("NOK", db_session, manager_id="individual", watch_conviction=WatchConviction.STANDARD)
    registry.upsert("NOK", db_session, manager_id="individual", watch_conviction=WatchConviction.HEAVY)
    row = registry.get("NOK", db_session, manager_id="individual")
    assert row is not None
    assert row.conviction_score == 3.0


def test_watchlist_prune_stale(db_session) -> None:
    registry = WatchlistRegistry(max_conviction_score=5.0, stale_days=30)
    stale = WatchlistEntry(
        manager_id="individual",
        ticker="OLD",
        conviction_score=1.0,
        last_seen_at=datetime.now(timezone.utc) - timedelta(days=45),
        watch_conviction=WatchConviction.STANDARD.value,
    )
    fresh = WatchlistEntry(
        manager_id="individual",
        ticker="NEW",
        conviction_score=1.0,
        last_seen_at=datetime.now(timezone.utc),
        watch_conviction=WatchConviction.STANDARD.value,
    )
    db_session.add_all([stale, fresh])
    db_session.commit()
    removed = registry.prune_stale(db_session, ["individual"])
    assert removed == 1
    assert registry.get("OLD", db_session, manager_id="individual") is None
    assert registry.get("NEW", db_session, manager_id="individual") is not None


def test_risk_buy_boosted_by_watchlist(db_session) -> None:
    registry = WatchlistRegistry(max_conviction_score=5.0, stale_days=30)
    registry.upsert("NVDA", db_session, manager_id="individual", watch_conviction=WatchConviction.HEAVY)
    manager = RiskManager(
        RiskConfig(
            seed_tickers={"NVDA"},
            max_trade_size_usd=1000,
            default_trade_size_usd=100,
            new_ticker_size_multiplier=10,
            cooldown_seconds=300,
            duplicate_window_seconds=300,
            trading_window_enabled=False,
        ),
        watchlist=registry,
    )
    from app.models.schemas import TradeSignal
    from app.parsing.buy_conviction import BuyConviction

    signal = TradeSignal(
        source_tweet_id="t-watch",
        ticker="NVDA",
        action=SignalAction.BUY,
        confidence=0.81,
        raw_text="adding NVDA",
        buy_conviction=BuyConviction.RELOAD,
    )
    result = manager.evaluate(signal, db_session, manager_id="individual", cash_available_usd=5000.0)
    assert result.allowed
    base = 100 + ((1000 * 0.75) - 100) * ((0.81 - 0.5) / 0.49)
    boost = watch_size_multiplier(WatchConviction.HEAVY, 2.0)
    assert result.normalized_trade_usd == round(min(1000, base * boost), 2)
