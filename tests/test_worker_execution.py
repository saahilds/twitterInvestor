import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import Settings
from app.models.db_models import SignalAction
from app.services.account_manager import AccountManager


@pytest.mark.asyncio
async def test_worker_buy_uses_limit_at_ask() -> None:
    settings = Settings(
        order_execution_mode="limit_at_ask",
        simulation_mode=True,
        enable_live_trading=False,
    )
    broker = MagicMock()
    broker.buy_limit_at_ask = AsyncMock(
        return_value=MagicMock(
            status="simulated",
            order_id="sim-1",
            simulation=True,
            quantity=0.01,
            raw_response={},
        )
    )

    from app.config.account_managers import AccountManagerConfig

    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=broker,
        risk_manager=MagicMock(),
        session_factory=MagicMock(),
        logger=logging.getLogger("test"),
    )

    await manager._execute_order(SignalAction.BUY, "NVDA", 1.0)
    broker.buy_limit_at_ask.assert_awaited_once_with(ticker="NVDA", amount_usd=1.0)
    broker.buy_market.assert_not_called()


@pytest.mark.asyncio
async def test_worker_executes_live_sell() -> None:
    settings = Settings(
        simulation_mode=False,
        enable_live_trading=True,
    )
    broker = MagicMock()
    broker.sell_market = AsyncMock(
        return_value=MagicMock(
            status="submitted",
            order_id="live-sell-1",
            simulation=False,
            raw_response={},
        )
    )

    from app.config.account_managers import AccountManagerConfig

    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=broker,
        risk_manager=MagicMock(),
        session_factory=MagicMock(),
        logger=logging.getLogger("test"),
    )

    await manager._execute_order(SignalAction.SELL, "NVDA", 250.0, sell_quantity=5.0)
    broker.sell_market.assert_awaited_once_with(ticker="NVDA", quantity=5.0, amount_usd=250.0)


@pytest.mark.asyncio
async def test_execution_uses_tweet_time_for_market_hours() -> None:
    from datetime import datetime, timezone

    from app.config.account_managers import AccountManagerConfig
    from app.models.schemas import IngestedTweet, RiskCheckResult, TradeSignal

    posted_at = datetime(2026, 5, 23, 16, 0, tzinfo=timezone.utc)
    risk = MagicMock()
    risk.evaluate.return_value = RiskCheckResult(allowed=False, reason="outside_market_hours")
    db = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = db
    settings = Settings(simulation_mode=True, enable_live_trading=False, broker_backend="mock")
    manager = AccountManager(
        config=AccountManagerConfig(id="individual", robinhood_account="individual"),
        settings=settings,
        broker=MagicMock(),
        risk_manager=risk,
        session_factory=lambda: session,
        logger=logging.getLogger("test"),
    )
    ingested = IngestedTweet(
        tweet_pk=1,
        tweet_id="overnight",
        account="CKCapitalxx",
        text="buying $NVDA",
        posted_at=posted_at,
        fetched_at=posted_at,
        is_reply=False,
        is_retweet=False,
    )
    signal = TradeSignal(
        source_tweet_id="overnight",
        ticker="NVDA",
        action=SignalAction.BUY,
        raw_text="buying $NVDA",
    )

    result = await manager.evaluate_and_execute(signal, ingested)

    assert result.allowed is False
    assert risk.evaluate.call_args.kwargs["as_of"] == posted_at


@pytest.mark.asyncio
async def test_outside_market_hours_parks_chrome_instead_of_polling(tmp_path, monkeypatch) -> None:
    from app.services.worker import BotOrchestrator

    settings = Settings(
        trading_window_enabled=True,
        poll_only_during_market_hours=True,
        poll_interval_seconds=60,
        snapshot_enabled=False,
        daily_digest_enabled=False,
        log_file=str(tmp_path / "bot.log"),
        broker_backend="mock",
        playwright_cdp_url="http://127.0.0.1:9223",
    )
    stopped: list[bool] = []

    class Client:
        async def shutdown_attached_browser(self) -> None:
            return

    ingestion = MagicMock()
    ingestion.twitter_client = Client()
    ingestion.consecutive_failures = 0
    orchestrator = BotOrchestrator(
        settings=settings,
        ingestion_service=ingestion,
        parser=MagicMock(),
        managers=[],
        session_factory=MagicMock(),
        audit_logger=MagicMock(),
        logger=logging.getLogger("test"),
    )
    monkeypatch.setattr(orchestrator, "_should_poll_feed", lambda: False)
    monkeypatch.setattr(orchestrator, "_run_maintenance", AsyncMock())
    monkeypatch.setattr(
        "app.services.worker.quit_bot_chrome",
        lambda settings: stopped.append(True) or True,
    )

    async def stop_after_idle(_seconds: float) -> None:
        orchestrator._running = False

    monkeypatch.setattr("app.services.worker.asyncio.sleep", stop_after_idle)
    orchestrator._running = True

    await orchestrator._run_loop()

    assert stopped == [True]
    ingestion.poll.assert_not_called()
