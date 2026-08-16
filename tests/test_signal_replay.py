from __future__ import annotations

from datetime import datetime, timezone

from app.models.db_models import ParsedSignal, SignalAction, Trade, Tweet
from app.parsing.signal_parser import RuleBasedSignalParser
from app.services.signal_replay import SignalReplayService
from app.testing.risk_config import make_risk_config
from app.risk.risk_manager import RiskManager


def test_replay_compare_stored_detects_drift(session_factory, db_session) -> None:
    tweet = Tweet(
        tweet_id="1001",
        account="CKCapitalxx",
        text="adding $NVDA starter here",
        posted_at=datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc),
        is_reply=False,
        is_retweet=False,
        url=None,
    )
    db_session.add(tweet)
    db_session.flush()
    db_session.add(
        ParsedSignal(
            tweet_pk=tweet.id,
            source_tweet_id=tweet.tweet_id,
            ticker="NVDA",
            action=SignalAction.IGNORE,
            confidence=0.1,
            strength="none",
            score=0,
            raw_text=tweet.text,
            suggested_trade_usd=0.0,
            manager_id="individual",
        )
    )
    db_session.commit()

    parser = RuleBasedSignalParser(known_tickers={"NVDA"})
    service = SignalReplayService(
        session_factory=session_factory,
        parser=parser,
        risk_manager=RiskManager(make_risk_config()),
        manager_id="individual",
    )
    rows = service.replay(compare_stored=True, include_risk=True, assume_cash=5_000)
    assert len(rows) == 1
    assert rows[0].parser_drift
    assert rows[0].mismatch
    assert any(s.action == "BUY" for s in rows[0].replayed)


def test_replay_marks_traded(session_factory, db_session) -> None:
    tweet = Tweet(
        tweet_id="1002",
        account="CKCapitalxx",
        text="trim $META",
        posted_at=datetime(2026, 7, 24, 16, 0, tzinfo=timezone.utc),
        is_reply=False,
        is_retweet=False,
        url=None,
    )
    db_session.add(tweet)
    db_session.flush()
    signal = ParsedSignal(
        tweet_pk=tweet.id,
        source_tweet_id=tweet.tweet_id,
        ticker="META",
        action=SignalAction.SELL,
        confidence=0.9,
        strength="strong",
        score=5,
        raw_text=tweet.text,
        suggested_trade_usd=0.0,
        manager_id="individual",
    )
    db_session.add(signal)
    db_session.flush()
    db_session.add(
        Trade(
            parsed_signal_id=signal.id,
            source_tweet_id=tweet.tweet_id,
            ticker="META",
            action=SignalAction.SELL,
            amount_usd=100.0,
            status="simulated",
            simulation=True,
            manager_id="individual",
        )
    )
    db_session.commit()

    parser = RuleBasedSignalParser(known_tickers={"META"})
    service = SignalReplayService(
        session_factory=session_factory,
        parser=parser,
        risk_manager=None,
        manager_id="individual",
    )
    rows = service.replay(include_risk=False)
    assert rows[0].traded
    assert not rows[0].traded_live
