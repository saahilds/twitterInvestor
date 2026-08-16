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


def _tweet(posted_at: datetime, tweet_id: str, *, text: str | None = None) -> Tweet:
    return Tweet(
        tweet_id=tweet_id,
        account="test",
        text=text if text is not None else f"tweet {tweet_id}",
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


def test_fetch_dashboard_tweets_filters_by_ticker_prefix(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    aapl = _tweet(now - timedelta(hours=1), "aapl")
    msft = _tweet(now - timedelta(hours=2), "msft")
    aa = _tweet(now - timedelta(hours=3), "aa")
    db_session.add_all([aapl, msft, aa])
    db_session.flush()
    db_session.add_all(
        [
            _signal(aapl, SignalAction.BUY, ticker="AAPL"),
            _signal(msft, SignalAction.BUY, ticker="MSFT"),
            _signal(aa, SignalAction.BUY, ticker="AA"),
        ]
    )
    db_session.commit()

    rows = fetch_dashboard_tweets(db_session, since=None, until=now, limit=50, ticker="AA")
    assert [row.tweet_id for row in rows] == ["aapl", "aa"]


def test_fetch_dashboard_tweets_ticker_filter_before_limit(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    old_match = _tweet(now - timedelta(days=5), "old-aapl")
    recent = [_tweet(now - timedelta(hours=i + 1), f"noise-{i}") for i in range(5)]
    db_session.add(old_match)
    db_session.add_all(recent)
    db_session.flush()
    db_session.add(_signal(old_match, SignalAction.BUY, ticker="AAPL"))
    for tweet in recent:
        db_session.add(_signal(tweet, SignalAction.BUY, ticker="MSFT"))
    db_session.commit()

    rows = fetch_dashboard_tweets(db_session, since=None, until=now, limit=3, ticker="AAPL")
    assert [row.tweet_id for row in rows] == ["old-aapl"]


def test_fetch_dashboard_tweets_matches_non_latest_signal_ticker(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweet = _tweet(now - timedelta(hours=1), "multi-ticker")
    db_session.add(tweet)
    db_session.flush()
    db_session.add_all(
        [
            ParsedSignal(
                tweet=tweet,
                source_tweet_id=tweet.tweet_id,
                ticker="AAOI",
                action=SignalAction.IGNORE,
                confidence=0.1,
                strength="none",
                score=0,
                raw_text=tweet.text,
                suggested_trade_usd=0.0,
                created_at=now - timedelta(minutes=10),
            ),
            ParsedSignal(
                tweet=tweet,
                source_tweet_id=tweet.tweet_id,
                ticker="NBIS",
                action=SignalAction.IGNORE,
                confidence=0.1,
                strength="none",
                score=0,
                raw_text=tweet.text,
                suggested_trade_usd=0.0,
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    rows = fetch_dashboard_tweets(db_session, since=None, until=now, limit=50, ticker="AAOI")
    assert [row.tweet_id for row in rows] == ["multi-ticker"]


def test_fetch_dashboard_tweets_matches_cashtag_in_text(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    body = (
        "I just want to share some updates on the Autopilot portfolio.\n\n"
        "$NBIS\n and \n$AAOI\n doing the heavy lifting."
    )
    match = _tweet(now - timedelta(hours=1), "holdings", text=body)
    other = _tweet(now - timedelta(hours=2), "other", text="Holding $MSFT only")
    db_session.add_all([match, other])
    db_session.flush()
    db_session.add_all(
        [
            _signal(match, SignalAction.IGNORE, ticker=None),
            _signal(other, SignalAction.IGNORE, ticker=None),
        ]
    )
    db_session.commit()

    rows = fetch_dashboard_tweets(db_session, since=None, until=now, limit=50, ticker="AAOI")
    assert [row.tweet_id for row in rows] == ["holdings"]


def test_fetch_dashboard_tweets_ticker_text_excludes_unrelated(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    tweet = _tweet(now - timedelta(hours=1), "rk", text="Love $RKLB lately")
    db_session.add(tweet)
    db_session.flush()
    db_session.add(_signal(tweet, SignalAction.WATCH, ticker="RKLB"))
    db_session.commit()

    rows = fetch_dashboard_tweets(db_session, since=None, until=now, limit=50, ticker="AAOI")
    assert rows == []


def test_text_mentions_ticker_helper() -> None:
    from app.parsing.ticker_mentions import text_mentions_ticker

    body = "$NBIS\n and \n$AAOI\n doing the heavy lifting."
    assert text_mentions_ticker(body, "AAOI") is True
    assert text_mentions_ticker(body, "AA") is True
    assert text_mentions_ticker(body, "NBIS") is True
    assert text_mentions_ticker(body, "AMD") is False
    assert text_mentions_ticker("Bought AMD today", "AMD") is True
    assert text_mentions_ticker("Bought $AMD today", "amd") is True


def test_fetch_dashboard_tweets_traded_filter(db_session) -> None:
    from app.models.db_models import Trade

    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    traded_tweet = _tweet(now - timedelta(hours=1), "traded")
    plain_tweet = _tweet(now - timedelta(hours=2), "plain")
    db_session.add_all([traded_tweet, plain_tweet])
    db_session.flush()
    traded_signal = _signal(traded_tweet, SignalAction.BUY, ticker="AAPL")
    plain_signal = _signal(plain_tweet, SignalAction.BUY, ticker="MSFT")
    db_session.add_all([traded_signal, plain_signal])
    db_session.flush()
    db_session.add(
        Trade(
            parsed_signal_id=traded_signal.id,
            source_tweet_id=traded_tweet.tweet_id,
            ticker="AAPL",
            action=SignalAction.BUY,
            amount_usd=10.0,
            status="simulated",
            simulation=True,
            manager_id="individual",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    traded = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, traded_filter="traded"
    )
    not_traded = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, traded_filter="not_traded"
    )
    assert [row.tweet_id for row in traded] == ["traded"]
    assert [row.tweet_id for row in not_traded] == ["plain"]


def test_fetch_dashboard_tweets_sort_oldest_and_confidence(db_session) -> None:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    low = _tweet(now - timedelta(hours=1), "low")
    high = _tweet(now - timedelta(hours=2), "high")
    db_session.add_all([low, high])
    db_session.flush()
    db_session.add_all(
        [
            ParsedSignal(
                tweet=low,
                source_tweet_id=low.tweet_id,
                ticker="AAPL",
                action=SignalAction.BUY,
                confidence=0.2,
                strength="weak",
                score=1,
                raw_text=low.text,
                suggested_trade_usd=1.0,
                created_at=low.posted_at,
            ),
            ParsedSignal(
                tweet=high,
                source_tweet_id=high.tweet_id,
                ticker="MSFT",
                action=SignalAction.BUY,
                confidence=0.95,
                strength="strong",
                score=5,
                raw_text=high.text,
                suggested_trade_usd=1.0,
                created_at=high.posted_at,
            ),
        ]
    )
    db_session.commit()

    oldest = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, sort="oldest"
    )
    by_conf = fetch_dashboard_tweets(
        db_session, since=None, until=now, limit=50, sort="confidence_desc"
    )
    assert [row.tweet_id for row in oldest] == ["high", "low"]
    assert [row.tweet_id for row in by_conf] == ["high", "low"]
