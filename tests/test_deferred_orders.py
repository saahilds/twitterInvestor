from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config.account_managers import AccountManagerConfig
from app.config.settings import Settings
from app.models.db_models import ParsedSignal, SignalAction, Tweet
from app.models.schemas import BrokerOrderResult, IngestedTweet, RiskCheckResult, TradeSignal
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.risk.market_hours import next_full_session_close_after
from app.services.account_manager import (
    DEFERRED_EXPIRED,
    DEFERRED_QUEUED,
    DEFERRED_RELEASED,
    QUEUED_FOR_MARKET_OPEN,
    AccountManager,
)
from app.testing.risk_config import make_risk_config
from app.risk.risk_manager import RiskManager

ET = ZoneInfo("America/New_York")

NFLX_MULTI = """Trade

Hey guys,

Decided to hedge myself a bit here.

Added 
$NFLX
 with a 3% position at $67.23
Added 
$UBER
 with 2% at $71.60
Added 
$HOOD
 1% at $85.85.

Sold the 
$ALAB
 swing at $210.26.
"""


def test_nflx_multi_allocations_parsed() -> None:
    parser = HybridSignalParser(known_tickers=["NFLX", "UBER", "HOOD", "ALAB"])
    signals = parser.parse(NFLX_MULTI, source_tweet_id="alloc-1")
    by_ticker = {s.ticker: s for s in signals if s.action != SignalAction.IGNORE}
    assert by_ticker["NFLX"].action == SignalAction.BUY
    assert by_ticker["NFLX"].portfolio_allocation_pct == 3.0
    assert by_ticker["UBER"].action == SignalAction.BUY
    assert by_ticker["UBER"].portfolio_allocation_pct == 2.0
    assert by_ticker["HOOD"].action == SignalAction.BUY
    assert by_ticker["HOOD"].portfolio_allocation_pct == 1.0
    assert by_ticker["ALAB"].action == SignalAction.SELL
    assert by_ticker["ALAB"].sell_fraction == 1.0


@pytest.mark.asyncio
async def test_outside_hours_queues_for_open(session_factory) -> None:
    posted_at = datetime(2026, 11, 5, 8, 58, tzinfo=ET)
    risk = RiskManager(make_risk_config(trading_window_enabled=True))
    settings = Settings(
        simulation_mode=True,
        enable_live_trading=False,
        broker_backend="mock",
        trading_window_enabled=True,
        defer_outside_market_hours=True,
        ck_portfolio_usd=10_000.0,
    )
    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=MagicMock(),
        risk_manager=risk,
        session_factory=session_factory,
        logger=logging.getLogger("test"),
    )

    with session_factory() as db:
        tweet = Tweet(
            tweet_id="premarket-1",
            account="CKCapitalxx",
            text="Added $NFLX with a 3% position at $67.23",
            posted_at=posted_at,
            fetched_at=posted_at,
        )
        db.add(tweet)
        db.commit()
        db.refresh(tweet)
        tweet_pk = tweet.id

    ingested = IngestedTweet(
        tweet_pk=tweet_pk,
        tweet_id="premarket-1",
        account="CKCapitalxx",
        text="Added $NFLX with a 3% position at $67.23",
        posted_at=posted_at,
        fetched_at=posted_at,
        is_reply=False,
        is_retweet=False,
    )
    signal = TradeSignal(
        source_tweet_id="premarket-1",
        ticker="NFLX",
        action=SignalAction.BUY,
        raw_text="Added $NFLX with a 3% position at $67.23",
        portfolio_allocation_pct=3.0,
        confidence=0.8,
        strength="medium",
        score=3,
    )

    result = await manager.evaluate_and_execute(signal, ingested)
    assert result.allowed is False
    assert result.rejection_reason == QUEUED_FOR_MARKET_OPEN

    with session_factory() as db:
        row = db.execute(select(ParsedSignal).where(ParsedSignal.id == result.parsed_signal_id)).scalar_one()
        assert row.deferred_status == DEFERRED_QUEUED
        assert row.portfolio_allocation_pct == 3.0
        assert row.deferred_expires_at is not None
        expected_expiry = next_full_session_close_after(posted_at).astimezone(timezone.utc)
        got = row.deferred_expires_at
        if got.tzinfo is None:
            got = got.replace(tzinfo=timezone.utc)
        assert got.astimezone(timezone.utc) == expected_expiry


@pytest.mark.asyncio
async def test_queued_signal_releases_at_open(session_factory) -> None:
    posted_at = datetime(2026, 11, 5, 8, 58, tzinfo=ET)
    open_at = datetime(2026, 11, 5, 9, 35, tzinfo=ET)
    risk = RiskManager(make_risk_config(trading_window_enabled=True, ck_portfolio_usd=10_000.0))
    broker = MagicMock()
    broker.buy_limit_at_ask = AsyncMock(
        return_value=BrokerOrderResult(
            status="simulated",
            order_id="sim-open",
            simulation=True,
            quantity=1.0,
            raw_response={},
        )
    )
    settings = Settings(
        simulation_mode=True,
        enable_live_trading=False,
        broker_backend="mock",
        trading_window_enabled=True,
        defer_outside_market_hours=True,
        order_execution_mode="limit_at_ask",
        ck_portfolio_usd=10_000.0,
    )
    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=broker,
        risk_manager=risk,
        session_factory=session_factory,
        logger=logging.getLogger("test"),
    )

    with session_factory() as db:
        tweet = Tweet(
            tweet_id="premarket-2",
            account="CKCapitalxx",
            text="Added $NFLX with a 3% position at $67.23",
            posted_at=posted_at,
            fetched_at=posted_at,
        )
        db.add(tweet)
        db.commit()
        db.refresh(tweet)
        tweet_pk = tweet.id

    ingested = IngestedTweet(
        tweet_pk=tweet_pk,
        tweet_id="premarket-2",
        account="CKCapitalxx",
        text="Added $NFLX with a 3% position at $67.23",
        posted_at=posted_at,
        fetched_at=posted_at,
        is_reply=False,
        is_retweet=False,
    )
    signal = TradeSignal(
        source_tweet_id="premarket-2",
        ticker="NFLX",
        action=SignalAction.BUY,
        raw_text="Added $NFLX with a 3% position at $67.23",
        portfolio_allocation_pct=3.0,
        confidence=0.9,
        strength="medium",
        score=4,
    )
    queued = await manager.evaluate_and_execute(signal, ingested)
    assert queued.rejection_reason == QUEUED_FOR_MARKET_OPEN

    handled = await manager.release_queued_signals(now=open_at)
    assert handled == 1
    broker.buy_limit_at_ask.assert_awaited()

    with session_factory() as db:
        row = db.get(ParsedSignal, queued.parsed_signal_id)
        assert row is not None
        assert row.deferred_status == DEFERRED_RELEASED
        assert row.rejection_reason is None


@pytest.mark.asyncio
async def test_queued_signal_expires_after_session(session_factory) -> None:
    posted_at = datetime(2026, 11, 5, 8, 58, tzinfo=ET)
    after_expiry = datetime(2026, 11, 5, 16, 1, tzinfo=ET)
    risk = MagicMock()
    risk.evaluate.return_value = RiskCheckResult(allowed=False, reason="outside_market_hours")
    settings = Settings(
        simulation_mode=True,
        enable_live_trading=False,
        broker_backend="mock",
        trading_window_enabled=True,
        defer_outside_market_hours=True,
        ck_portfolio_usd=10_000.0,
    )
    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=MagicMock(),
        risk_manager=risk,
        session_factory=session_factory,
        logger=logging.getLogger("test"),
    )

    with session_factory() as db:
        tweet = Tweet(
            tweet_id="premarket-3",
            account="CKCapitalxx",
            text="Added $UBER with 2% at $71.60",
            posted_at=posted_at,
            fetched_at=posted_at,
        )
        db.add(tweet)
        db.commit()
        db.refresh(tweet)
        tweet_pk = tweet.id

    ingested = IngestedTweet(
        tweet_pk=tweet_pk,
        tweet_id="premarket-3",
        account="CKCapitalxx",
        text="Added $UBER with 2% at $71.60",
        posted_at=posted_at,
        fetched_at=posted_at,
        is_reply=False,
        is_retweet=False,
    )
    signal = TradeSignal(
        source_tweet_id="premarket-3",
        ticker="UBER",
        action=SignalAction.BUY,
        raw_text="Added $UBER with 2% at $71.60",
        portfolio_allocation_pct=2.0,
    )
    queued = await manager.evaluate_and_execute(signal, ingested)
    handled = await manager.release_queued_signals(now=after_expiry)
    assert handled == 1

    with session_factory() as db:
        row = db.get(ParsedSignal, queued.parsed_signal_id)
        assert row is not None
        assert row.deferred_status == DEFERRED_EXPIRED
        assert row.rejection_reason == "deferred_expired"
