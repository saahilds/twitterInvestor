from __future__ import annotations

from datetime import datetime, timezone

from app.models.db_models import ParsedSignal, ParserFeedback, SignalAction, Tweet
from app.parsing.buy_conviction import parse_portfolio_weight_pct


def test_parse_portfolio_weight_pct() -> None:
    assert parse_portfolio_weight_pct("i just entered $ADEA at a 5% weight") == 5.0


def test_parser_feedback_model(db_session) -> None:
    tweet = Tweet(
        tweet_id="fb1",
        account="CKCapitalxx",
        text="watching $TSLA",
        posted_at=datetime.now(timezone.utc),
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
            ticker="TSLA",
            action=SignalAction.BUY,
            confidence=0.5,
            strength="weak",
            score=1,
            raw_text=tweet.text,
            suggested_trade_usd=0.0,
            manager_id="individual",
        )
    )
    db_session.add(
        ParserFeedback(
            tweet_id=tweet.tweet_id,
            tweet_text=tweet.text,
            parser_action=SignalAction.BUY,
            parser_ticker="TSLA",
            correct_action=SignalAction.IGNORE,
            note="commentary",
        )
    )
    db_session.commit()
    row = db_session.query(ParserFeedback).one()
    assert row.correct_action == SignalAction.IGNORE
