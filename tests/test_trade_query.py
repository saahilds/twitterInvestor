from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.db_models import ParsedSignal, SignalAction, Trade, Tweet
from app.services.trade_query import fetch_dashboard_trades


def _tweet(posted_at: datetime, tweet_id: str) -> Tweet:
    return Tweet(
        tweet_id=tweet_id,
        account="test",
        text=f"tweet {tweet_id}",
        posted_at=posted_at,
        fetched_at=posted_at,
        is_reply=False,
        is_retweet=False,
    )


def _trade(
    *,
    signal: ParsedSignal,
    ticker: str,
    action: SignalAction,
    amount_usd: float,
    status: str,
    simulation: bool,
    created_at: datetime,
    manager_id: str = "individual",
) -> Trade:
    return Trade(
        parsed_signal_id=signal.id,
        source_tweet_id=signal.source_tweet_id,
        ticker=ticker,
        action=action,
        amount_usd=amount_usd,
        status=status,
        simulation=simulation,
        manager_id=manager_id,
        created_at=created_at,
        updated_at=created_at,
    )


def _seed_signal(db_session, tweet: Tweet, *, ticker: str = "AAPL") -> ParsedSignal:
    signal = ParsedSignal(
        tweet=tweet,
        source_tweet_id=tweet.tweet_id,
        ticker=ticker,
        action=SignalAction.BUY,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text=tweet.text,
        suggested_trade_usd=1.0,
        created_at=tweet.posted_at,
    )
    db_session.add(signal)
    db_session.flush()
    return signal


def test_fetch_dashboard_trades_filters_action_status_mode(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweet = _tweet(now, "t1")
    db_session.add(tweet)
    db_session.flush()
    signal = _seed_signal(db_session, tweet)
    db_session.add_all(
        [
            _trade(
                signal=signal,
                ticker="AAPL",
                action=SignalAction.BUY,
                amount_usd=10,
                status="filled",
                simulation=False,
                created_at=now - timedelta(hours=1),
            ),
            _trade(
                signal=signal,
                ticker="MSFT",
                action=SignalAction.SELL,
                amount_usd=20,
                status="simulated",
                simulation=True,
                created_at=now - timedelta(hours=2),
            ),
            _trade(
                signal=signal,
                ticker="NVDA",
                action=SignalAction.BUY,
                amount_usd=30,
                status="failed",
                simulation=False,
                created_at=now - timedelta(hours=3),
            ),
        ]
    )
    db_session.commit()

    buys = fetch_dashboard_trades(
        db_session,
        manager_id="individual",
        since=None,
        until=now,
        action_filter="buy",
    )
    assert [row.ticker for row in buys] == ["AAPL", "NVDA"]

    live = fetch_dashboard_trades(
        db_session,
        manager_id="individual",
        since=None,
        until=now,
        mode_filter="live",
    )
    assert [row.ticker for row in live] == ["AAPL", "NVDA"]

    failed = fetch_dashboard_trades(
        db_session,
        manager_id="individual",
        since=None,
        until=now,
        status_filter="failed",
    )
    assert [row.ticker for row in failed] == ["NVDA"]


def test_fetch_dashboard_trades_ticker_before_limit(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweet = _tweet(now, "t1")
    db_session.add(tweet)
    db_session.flush()
    signal = _seed_signal(db_session, tweet)
    old = _trade(
        signal=signal,
        ticker="AAPL",
        action=SignalAction.BUY,
        amount_usd=5,
        status="filled",
        simulation=True,
        created_at=now - timedelta(days=10),
    )
    noise = [
        _trade(
            signal=signal,
            ticker="MSFT",
            action=SignalAction.BUY,
            amount_usd=1,
            status="filled",
            simulation=True,
            created_at=now - timedelta(hours=i + 1),
        )
        for i in range(5)
    ]
    db_session.add(old)
    db_session.add_all(noise)
    db_session.commit()

    rows = fetch_dashboard_trades(
        db_session,
        manager_id="individual",
        since=None,
        until=now,
        limit=3,
        ticker="AAPL",
    )
    assert [row.ticker for row in rows] == ["AAPL"]


def test_fetch_dashboard_trades_sort_amount_and_oldest(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweet = _tweet(now, "t1")
    db_session.add(tweet)
    db_session.flush()
    signal = _seed_signal(db_session, tweet)
    db_session.add_all(
        [
            _trade(
                signal=signal,
                ticker="AAA",
                action=SignalAction.BUY,
                amount_usd=5,
                status="filled",
                simulation=True,
                created_at=now - timedelta(hours=1),
            ),
            _trade(
                signal=signal,
                ticker="ZZZ",
                action=SignalAction.BUY,
                amount_usd=50,
                status="filled",
                simulation=True,
                created_at=now - timedelta(hours=3),
            ),
        ]
    )
    db_session.commit()

    by_amount = fetch_dashboard_trades(
        db_session,
        manager_id="individual",
        since=None,
        until=now,
        sort="amount_desc",
    )
    oldest = fetch_dashboard_trades(
        db_session,
        manager_id="individual",
        since=None,
        until=now,
        sort="oldest",
    )
    assert [row.ticker for row in by_amount] == ["ZZZ", "AAA"]
    assert [row.ticker for row in oldest] == ["ZZZ", "AAA"]
