from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.account_managers import AccountManagerConfig, parse_bot_managers
from app.config.settings import Settings
from app.execution.mock_broker import MockBroker
from app.models.db_models import ParsedSignal, SignalAction, Trade, Tweet
from app.models.schemas import IngestedTweet, TradeSignal
from app.risk.risk_manager import RiskConfig, RiskManager
from app.services.account_manager import AccountManager


def test_parse_bot_managers_multiple() -> None:
    settings = Settings(bot_managers="individual,joint", robinhood_account=None, bot_managers_enable_all=True)
    configs = parse_bot_managers(settings)
    assert [cfg.id for cfg in configs] == ["individual", "joint"]
    assert all(cfg.enabled for cfg in configs)


def test_parse_bot_managers_only_primary_enabled_by_default() -> None:
    settings = Settings(bot_managers="individual,joint", robinhood_account="joint")
    configs = parse_bot_managers(settings)
    enabled = {cfg.id: cfg.enabled for cfg in configs}
    assert enabled == {"individual": False, "joint": True}


def test_parse_bot_managers_explicit_disable() -> None:
    settings = Settings(bot_managers="individual:disabled,joint", robinhood_account="joint")
    configs = parse_bot_managers(settings)
    enabled = {cfg.id: cfg.enabled for cfg in configs}
    assert enabled == {"individual": False, "joint": True}


@pytest.mark.asyncio
async def test_same_tweet_can_trade_on_two_managers(db_session) -> None:
    settings = Settings(
        simulation_mode=True,
        enable_live_trading=False,
        trading_window_enabled=False,
        cooldown_seconds=0,
        duplicate_window_seconds=0,
    )
    tweet_row = Tweet(
        tweet_id="tweet-multi",
        account="test",
        text="buying $NVDA",
        posted_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        is_reply=False,
        is_retweet=False,
    )
    db_session.add(tweet_row)
    db_session.commit()

    ingested = IngestedTweet(
        tweet_pk=tweet_row.id,
        tweet_id=tweet_row.tweet_id,
        account=tweet_row.account,
        text=tweet_row.text,
        posted_at=tweet_row.posted_at,
        fetched_at=tweet_row.fetched_at,
        is_reply=False,
        is_retweet=False,
    )
    signal = TradeSignal(
        source_tweet_id="tweet-multi",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="buying $NVDA",
        suggested_trade_usd=1.0,
    )

    risk_config = RiskConfig(
        seed_tickers={"NVDA"},
        max_trade_size_usd=5,
        default_trade_size_usd=1,
        new_ticker_size_multiplier=10,
        cooldown_seconds=0,
        duplicate_window_seconds=0,
        trading_window_enabled=False,
    )

    def _manager(manager_id: str) -> AccountManager:
        broker = MockBroker()
        return AccountManager(
            config=AccountManagerConfig(id=manager_id, robinhood_account=manager_id),
            settings=settings,
            broker=broker,
            risk_manager=RiskManager(risk_config),
            session_factory=lambda: db_session,
            logger=MagicMock(),
            trade_status_sync=None,
        )

    individual = _manager("individual")
    joint = _manager("joint")

    result_individual = await individual.evaluate_and_execute(signal, ingested)
    result_joint = await joint.evaluate_and_execute(signal, ingested)

    assert result_individual.allowed
    assert result_joint.allowed

    trades = db_session.query(Trade).order_by(Trade.manager_id.asc()).all()
    assert len(trades) == 2
    assert {trade.manager_id for trade in trades} == {"individual", "joint"}

    signals = db_session.query(ParsedSignal).all()
    assert len(signals) == 2
    assert {row.manager_id for row in signals} == {"individual", "joint"}


@pytest.mark.asyncio
async def test_duplicate_tweet_blocked_per_manager_not_globally(db_session) -> None:
    settings = Settings(
        simulation_mode=True,
        enable_live_trading=False,
        trading_window_enabled=False,
        cooldown_seconds=0,
        duplicate_window_seconds=0,
    )
    tweet_row = Tweet(
        tweet_id="tweet-dup",
        account="test",
        text="buying $NVDA",
        posted_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        is_reply=False,
        is_retweet=False,
    )
    db_session.add(tweet_row)
    db_session.commit()

    ingested = IngestedTweet(
        tweet_pk=tweet_row.id,
        tweet_id=tweet_row.tweet_id,
        account=tweet_row.account,
        text=tweet_row.text,
        posted_at=tweet_row.posted_at,
        fetched_at=tweet_row.fetched_at,
        is_reply=False,
        is_retweet=False,
    )
    signal = TradeSignal(
        source_tweet_id="tweet-dup",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="buying $NVDA",
        suggested_trade_usd=1.0,
    )

    risk_config = RiskConfig(
        seed_tickers={"NVDA"},
        max_trade_size_usd=5,
        default_trade_size_usd=1,
        new_ticker_size_multiplier=10,
        cooldown_seconds=0,
        duplicate_window_seconds=0,
        trading_window_enabled=False,
    )

    broker = MockBroker()
    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=broker,
        risk_manager=RiskManager(risk_config),
        session_factory=lambda: db_session,
        logger=MagicMock(),
    )

    first = await manager.evaluate_and_execute(signal, ingested)
    second = await manager.evaluate_and_execute(signal, ingested)

    assert first.allowed
    assert not second.allowed
    assert second.rejection_reason is not None
    assert second.rejection_reason.startswith("duplicate_tweet")


def test_orchestrator_pause_single_manager() -> None:
    import logging
    from unittest.mock import MagicMock

    from app.services.worker import BotOrchestrator

    settings = Settings()
    manager_a = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=MockBroker(),
        risk_manager=MagicMock(),
        session_factory=MagicMock(),
        logger=logging.getLogger("test"),
    )
    manager_b = AccountManager(
        config=AccountManagerConfig(id="joint", robinhood_account="joint"),
        settings=settings,
        broker=MockBroker(),
        risk_manager=MagicMock(),
        session_factory=MagicMock(),
        logger=logging.getLogger("test"),
    )
    orchestrator = BotOrchestrator(
        settings=settings,
        ingestion_service=MagicMock(),
        parser=MagicMock(),
        managers=[manager_a, manager_b],
        session_factory=MagicMock(),
        audit_logger=MagicMock(),
        logger=logging.getLogger("test"),
    )

    orchestrator.pause("individual")
    assert manager_a.paused
    assert not manager_b.paused
