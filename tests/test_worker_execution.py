import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import Settings
from app.models.db_models import SignalAction
from app.services.worker import BotWorker


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

    worker = BotWorker(
        settings=settings,
        ingestion_service=MagicMock(),
        parser=MagicMock(),
        risk_manager=MagicMock(),
        broker=broker,
        session_factory=MagicMock(),
        audit_logger=MagicMock(),
        logger=logging.getLogger("test"),
    )

    await worker._execute_order(SignalAction.BUY, "NVDA", 1.0)
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

    worker = BotWorker(
        settings=settings,
        ingestion_service=MagicMock(),
        parser=MagicMock(),
        risk_manager=MagicMock(),
        broker=broker,
        session_factory=MagicMock(),
        audit_logger=MagicMock(),
        logger=logging.getLogger("test"),
    )

    await worker._execute_order(SignalAction.SELL, "NVDA", 250.0)
    broker.sell_market.assert_awaited_once_with(ticker="NVDA", amount_usd=250.0)
