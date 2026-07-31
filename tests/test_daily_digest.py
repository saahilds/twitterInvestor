from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.db_models import ParsedSignal, SignalAction, Trade, Tweet  # noqa: F401
from app.risk.market_hours import DigestPeriod, digest_period_for
from app.services.daily_digest import DailyDigestService


ET = ZoneInfo("America/New_York")


def test_digest_period_boundaries() -> None:
    assert digest_period_for(datetime(2026, 7, 24, 8, 0, tzinfo=ET)) == DigestPeriod.PREMARKET
    assert digest_period_for(datetime(2026, 7, 24, 10, 0, tzinfo=ET)) == DigestPeriod.MORNING
    assert digest_period_for(datetime(2026, 7, 24, 12, 30, tzinfo=ET)) == DigestPeriod.HALFTIME
    assert digest_period_for(datetime(2026, 7, 24, 15, 10, tzinfo=ET)) == DigestPeriod.FOURTH_QUARTER
    assert digest_period_for(datetime(2026, 7, 24, 16, 0, tzinfo=ET)) == DigestPeriod.AFTER_HOURS
    assert digest_period_for(datetime(2026, 7, 24, 16, 30, tzinfo=ET)) == DigestPeriod.AFTER_HOURS


def test_digest_includes_alerts_and_trades_only(session_factory, db_session) -> None:
    ignore = Tweet(
        tweet_id="d1",
        account="CKCapitalxx",
        text="here is the thesis on $ADEA patents",
        posted_at=datetime(2026, 7, 24, 14, 0, tzinfo=ET),
        is_reply=False,
        is_retweet=False,
        url=None,
    )
    buy = Tweet(
        tweet_id="d2",
        account="CKCapitalxx",
        text="adding $NVDA starter",
        posted_at=datetime(2026, 7, 24, 14, 30, tzinfo=ET),
        is_reply=False,
        is_retweet=False,
        url=None,
    )
    blocked = Tweet(
        tweet_id="d3",
        account="CKCapitalxx",
        text="trim $META",
        posted_at=datetime(2026, 7, 24, 15, 0, tzinfo=ET),
        is_reply=False,
        is_retweet=False,
        url=None,
    )
    db_session.add_all([ignore, buy, blocked])
    db_session.flush()
    db_session.add(
        ParsedSignal(
            tweet_pk=ignore.id,
            source_tweet_id=ignore.tweet_id,
            ticker="ADEA",
            action=SignalAction.IGNORE,
            confidence=0.2,
            strength="none",
            score=0,
            raw_text=ignore.text,
            suggested_trade_usd=0.0,
            manager_id="individual",
        )
    )
    buy_signal = ParsedSignal(
        tweet_pk=buy.id,
        source_tweet_id=buy.tweet_id,
        ticker="NVDA",
        action=SignalAction.BUY,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text=buy.text,
        suggested_trade_usd=100.0,
        manager_id="individual",
    )
    db_session.add(buy_signal)
    db_session.flush()
    db_session.add(
        Trade(
            parsed_signal_id=buy_signal.id,
            source_tweet_id=buy.tweet_id,
            ticker="NVDA",
            action=SignalAction.BUY,
            amount_usd=100.0,
            status="simulated",
            simulation=True,
            manager_id="individual",
        )
    )
    db_session.add(
        ParsedSignal(
            tweet_pk=blocked.id,
            source_tweet_id=blocked.tweet_id,
            ticker="META",
            action=SignalAction.SELL,
            confidence=0.8,
            strength="strong",
            score=4,
            raw_text=blocked.text,
            suggested_trade_usd=0.0,
            rejection_reason="not_in_portfolio:META",
            manager_id="individual",
        )
    )
    db_session.commit()

    service = DailyDigestService(session_factory)
    now = datetime(2026, 7, 24, 15, 30, tzinfo=ET)
    row = service.rebuild("2026-07-24", force=True, now=now)
    assert row is not None
    assert row.trade_count == 1
    assert row.rejected_count == 1
    assert row.informational_count == 0
    payload = service.to_api_dict(row)
    md = payload["summary_markdown"]
    assert "Trade alerts" in md
    assert "Trades executed" in md
    assert "NVDA" in md
    assert "META" in md
    assert "ADEA" not in md
    assert "Informational" not in md
    assert "Premarket" not in md


def test_digest_finalize_marks_complete(session_factory) -> None:
    service = DailyDigestService(session_factory)
    now = datetime(2026, 7, 24, 20, 5, tzinfo=ET)
    row = service.finalize("2026-07-24", now=now)
    assert row is not None
    assert row.status == "complete"
