import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.account_managers import AccountManagerConfig
from app.config.settings import Settings
from app.execution.holdings import BrokerHolding, BrokerPortfolioMetrics
from app.execution.robinhood_broker import RobinhoodBroker
from app.models.db_models import SignalAction
from app.risk.risk_manager import RiskManager
from app.services.account_manager import AccountManager
from app.testing.risk_config import make_risk_config


def _portfolio_metrics() -> BrokerPortfolioMetrics:
    return BrokerPortfolioMetrics(
        stocks_plus_cash=10_000.0,
        portfolio_equity=10_000.0,
        positions_market_value=None,
        profile_market_value=None,
        cash=2_000.0,
    )


def _holding(*, quantity: float = 10.0, last_price: float = 50.0) -> BrokerHolding:
    return BrokerHolding(
        ticker="ASTS",
        quantity=quantity,
        average_cost=40.0,
        last_price=last_price,
        market_value=quantity * last_price,
        cost_basis=quantity * 40.0,
        unrealized_pnl=100.0,
        unrealized_pnl_pct=25.0,
    )


@pytest.mark.asyncio
async def test_resolve_live_sell_order_uses_current_holdings() -> None:
    broker = MagicMock(spec=RobinhoodBroker)
    broker.get_holding = MagicMock(return_value=(_holding(quantity=8.0), None))
    broker.get_portfolio_metrics = MagicMock(return_value=_portfolio_metrics())

    manager = AccountManager(
        config=AccountManagerConfig(id="joint", robinhood_account="joint"),
        settings=Settings(),
        broker=broker,
        risk_manager=RiskManager(make_risk_config(cooldown_seconds=0, duplicate_window_seconds=0)),
        session_factory=MagicMock(),
        logger=logging.getLogger("test"),
    )

    sizing = await manager._resolve_live_sell_order("ASTS", 1.0)

    assert sizing is not None
    assert sizing.quantity == 8.0
    broker.get_holding.assert_called_once_with("ASTS")


@pytest.mark.asyncio
async def test_resolve_live_sell_order_returns_none_when_flat() -> None:
    broker = MagicMock(spec=RobinhoodBroker)
    broker.get_holding = MagicMock(return_value=(None, None))
    broker.get_portfolio_metrics = MagicMock(return_value=_portfolio_metrics())

    manager = AccountManager(
        config=AccountManagerConfig(id="joint", robinhood_account="joint"),
        settings=Settings(),
        broker=broker,
        risk_manager=RiskManager(make_risk_config(cooldown_seconds=0, duplicate_window_seconds=0)),
        session_factory=MagicMock(),
        logger=logging.getLogger("test"),
    )

    assert await manager._resolve_live_sell_order("ASTS", 1.0) is None
