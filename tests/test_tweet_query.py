from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.db_models import ParsedSignal, SignalAction, Tweet
from app.services.tweet_query import (
    TweetWindowError,
    fetch_dashboard_tweets,
    normalize_signal_filter,
    resolve_tweet_window,
)


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


def _signal(tweet: Tweet, action: SignalAction, *, ticker: str | None = "AAPL") -> ParsedSignal:
    return ParsedSignal(
        tweet=tweet,
        source_tweet_id=tweet.tweet_id,
        ticker=ticker,
        action=action,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text=tweet.text,
        suggested_trade_usd=1.0,
        created_at=tweet.posted_at,
    )


def test_resolve_tweet_window_preset_1w() -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    since, until = resolve_tweet_window(range_key="1w", since=None, until=None, now=now)
    assert since == now - timedelta(days=7)
    assert until == now


def test_resolve_tweet_window_custom_overrides_preset() -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    custom_since = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    custom_until = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    since, until = resolve_tweet_window(
        range_key="1w",
        since=custom_since,
        until=custom_until,
        now=now,
    )
    assert since == custom_since
    assert until == custom_until


def test_resolve_tweet_window_custom_requires_both_bounds() -> None:
    with pytest.raises(TweetWindowError, match="custom_range_requires_since_and_until"):
        resolve_tweet_window(
            range_key="1w",
            since=datetime(2026, 6, 1, tzinfo=timezone.utc),
            until=None,
        )


def test_resolve_tweet_window_rejects_invalid_order() -> None:
    since = datetime(2026, 6, 10, tzinfo=timezone.utc)
    until = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(TweetWindowError, match="since_must_be_before_until"):
        resolve_tweet_window(range_key="custom", since=since, until=until)


def test_resolve_tweet_window_all_has_no_lower_bound() -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    since, until = resolve_tweet_window(range_key="all", since=None, until=None, now=now)
    assert since is None
    assert until == now


def test_fetch_dashboard_tweets_filters_and_orders(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweets = [
        _tweet(now - timedelta(days=10), "old"),
        _tweet(now - timedelta(days=3), "mid"),
        _tweet(now - timedelta(hours=2), "new"),
        _tweet(now + timedelta(hours=1), "future"),
    ]
    db_session.add_all(tweets)
    db_session.commit()

    since = now - timedelta(days=7)
    rows = fetch_dashboard_tweets(db_session, since=since, until=now, limit=50)

    assert [row.tweet_id for row in rows] == ["new", "mid"]
    assert len(rows) == 2


def test_fetch_dashboard_tweets_respects_limit(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    for i in range(5):
        db_session.add(_tweet(now - timedelta(hours=i), f"id-{i}"))
    db_session.commit()

    rows = fetch_dashboard_tweets(db_session, since=None, until=now, limit=3)
    assert len(rows) == 3
    assert [row.tweet_id for row in rows] == ["id-0", "id-1", "id-2"]


def test_normalize_signal_filter_falls_back_to_all() -> None:
    assert normalize_signal_filter("buy") == "buy"
    assert normalize_signal_filter("invalid") == "all"


def test_fetch_dashboard_tweets_filters_by_latest_signal(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    buy_tweet = _tweet(now - timedelta(hours=1), "buy")
    sell_tweet = _tweet(now - timedelta(hours=2), "sell")
    ignore_tweet = _tweet(now - timedelta(hours=3), "ignore")
    unsignaled_tweet = _tweet(now - timedelta(hours=4), "plain")
    db_session.add_all([buy_tweet, sell_tweet, ignore_tweet, unsignaled_tweet])
    db_session.flush()
    db_session.add_all(
        [
            _signal(buy_tweet, SignalAction.BUY),
            _signal(sell_tweet, SignalAction.SELL),
            _signal(ignore_tweet, SignalAction.IGNORE, ticker=None),
        ]
    )
    db_session.commit()

    alerts = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, signal_filter="alerts"
    )
    buys = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, signal_filter="buy"
    )
    ignored = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, signal_filter="ignore"
    )

    assert [row.tweet_id for row in alerts] == ["buy", "sell"]
    assert [row.tweet_id for row in buys] == ["buy"]
    assert [row.tweet_id for row in ignored] == ["ignore"]


def test_fetch_dashboard_tweets_uses_latest_signal_when_multiple(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweet = _tweet(now - timedelta(hours=1), "multi")
    db_session.add(tweet)
    db_session.flush()
    db_session.add_all(
        [
            _signal(tweet, SignalAction.IGNORE, ticker=None),
            ParsedSignal(
                tweet=tweet,
                source_tweet_id=tweet.tweet_id,
                ticker="MSFT",
                action=SignalAction.BUY,
                confidence=0.8,
                strength="strong",
                score=4,
                raw_text=tweet.text,
                suggested_trade_usd=1.0,
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    rows = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, signal_filter="buy"
    )
    assert [row.tweet_id for row in rows] == ["multi"]
