from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.db_models import ParsedSignal, SignalAction, Tweet, TweetLabel


def _seed_flagged_signal(db) -> int:
    tweet = Tweet(
        tweet_id="rq-1",
        account="CKCapitalxx",
        text="🚨Trade🚨 $XYZ looking interesting",
        posted_at=datetime.now(timezone.utc),
        is_reply=False,
        is_retweet=False,
        url=None,
    )
    db.add(tweet)
    db.flush()
    signal = ParsedSignal(
        tweet_pk=tweet.id,
        source_tweet_id=tweet.tweet_id,
        ticker="XYZ",
        action=SignalAction.BUY,
        confidence=0.5,
        strength="weak",
        score=3,
        raw_text=tweet.text,
        suggested_trade_usd=0.0,
        needs_review=True,
        review_reason="trade_header_low_conf",
        manager_id="individual",
    )
    db.add(signal)
    db.commit()
    return signal.id


def _queue_items(db, *, limit: int = 50) -> list[ParsedSignal]:
    """Mirror GET /dashboard/review-queue filtering."""
    signals = (
        db.execute(
            select(ParsedSignal)
            .where(ParsedSignal.needs_review.is_(True))
            .order_by(ParsedSignal.created_at.desc())
            .limit(limit * 3)
        )
        .scalars()
        .all()
    )
    items: list[ParsedSignal] = []
    for signal in signals:
        label_stmt = select(TweetLabel).where(TweetLabel.tweet_id == signal.source_tweet_id)
        if signal.ticker:
            label_stmt = label_stmt.where(TweetLabel.ticker == signal.ticker)
        if db.execute(label_stmt.limit(1)).scalar_one_or_none() is not None:
            continue
        items.append(signal)
        if len(items) >= limit:
            break
    return items


def test_review_queue_returns_flagged_signal_and_label_clears(db_session) -> None:
    signal_id = _seed_flagged_signal(db_session)

    queued = _queue_items(db_session)
    assert len(queued) == 1
    assert queued[0].id == signal_id
    assert queued[0].ticker == "XYZ"
    assert queued[0].review_reason == "trade_header_low_conf"

    signal = db_session.get(ParsedSignal, signal_id)
    assert signal is not None
    label = TweetLabel(
        tweet_id=signal.source_tweet_id,
        ticker=signal.ticker,
        action=SignalAction.BUY,
        segment_text=signal.raw_text,
        labeled_by="dashboard",
    )
    db_session.add(label)
    signal.needs_review = False
    signal.review_reason = None
    db_session.commit()

    assert _queue_items(db_session) == []

    row = db_session.get(ParsedSignal, signal_id)
    assert row is not None
    assert row.needs_review is False
    labels = db_session.execute(
        select(TweetLabel).where(TweetLabel.tweet_id == "rq-1", TweetLabel.ticker == "XYZ")
    ).scalars().all()
    assert len(labels) == 1
    assert labels[0].action == SignalAction.BUY

    # Undo: delete labels and restore review flag.
    for existing in labels:
        db_session.delete(existing)
    row.needs_review = True
    row.review_reason = "trade_header_low_conf"
    db_session.commit()

    requeued = _queue_items(db_session)
    assert len(requeued) == 1
    assert requeued[0].id == signal_id
