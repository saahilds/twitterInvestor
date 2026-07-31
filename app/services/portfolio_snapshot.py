from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.execution.robinhood_broker import RobinhoodBroker
from app.execution.holdings import resolve_stocks_plus_cash
from app.risk.market_hours import is_within_extended_chart_hours
from app.services import portfolio_history


def maybe_record_snapshot(
    *,
    broker: RobinhoodBroker,
    settings: Settings,
    session_factory: Callable[[], Session],
    logger: logging.Logger,
) -> bool:
    """Record an account snapshot during extended chart hours. Returns True if written."""
    if not settings.snapshot_enabled:
        return False
    if not is_within_extended_chart_hours():
        return False

    holdings, metrics, error = broker.get_broker_snapshot()
    if error:
        logger.warning(
            "snapshot_broker_error",
            extra={"event_type": "snapshot", "error": error},
        )
        return False

    positions_market = sum(row.market_value or 0.0 for row in holdings)
    holdings_market = metrics.profile_market_value
    if holdings_market is None and holdings:
        holdings_market = positions_market
    stocks_plus_cash = metrics.stocks_plus_cash or resolve_stocks_plus_cash(
        portfolio_equity=metrics.portfolio_equity,
        profile_market_value=holdings_market,
        cash=metrics.cash,
    )
    if stocks_plus_cash is None:
        return False

    with session_factory() as db:
        portfolio_history.record_snapshot(
            db,
            account_number=broker._account_number,
            stocks_plus_cash=stocks_plus_cash,
            holdings_market_value=holdings_market,
            cash=metrics.cash,
        )
        db.commit()
    return True