from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import SignalAction, Trade
from app.services.tweet_query import (
    DEFAULT_TWEET_LIMIT,
    MAX_TWEET_LIMIT,
    normalize_ticker_prefix,
)

DEFAULT_TRADE_LIMIT = DEFAULT_TWEET_LIMIT
MAX_TRADE_LIMIT = MAX_TWEET_LIMIT
TRADE_ACTION_FILTERS = frozenset({"all", "buy", "sell"})
DEFAULT_TRADE_ACTION_FILTER = "all"
TRADE_STATUS_FILTERS = frozenset(
    {"all", "filled", "submitted", "simulated", "rejected", "failed"}
)
DEFAULT_TRADE_STATUS_FILTER = "all"
TRADE_MODE_FILTERS = frozenset({"all", "live", "sim"})
DEFAULT_TRADE_MODE_FILTER = "all"
TRADE_SORT_KEYS = frozenset(
    {"newest", "oldest", "ticker_asc", "ticker_desc", "amount_desc"}
)
DEFAULT_TRADE_SORT = "newest"
FAILED_TRADE_STATUSES = ("failed", "rejected")


def normalize_trade_action_filter(action_filter: str | None) -> str:
    key = (action_filter or DEFAULT_TRADE_ACTION_FILTER).strip().lower()
    return key if key in TRADE_ACTION_FILTERS else DEFAULT_TRADE_ACTION_FILTER


def normalize_trade_status_filter(status_filter: str | None) -> str:
    key = (status_filter or DEFAULT_TRADE_STATUS_FILTER).strip().lower()
    return key if key in TRADE_STATUS_FILTERS else DEFAULT_TRADE_STATUS_FILTER


def normalize_trade_mode_filter(mode_filter: str | None) -> str:
    key = (mode_filter or DEFAULT_TRADE_MODE_FILTER).strip().lower()
    return key if key in TRADE_MODE_FILTERS else DEFAULT_TRADE_MODE_FILTER


def normalize_trade_sort(sort: str | None) -> str:
    key = (sort or DEFAULT_TRADE_SORT).strip().lower()
    return key if key in TRADE_SORT_KEYS else DEFAULT_TRADE_SORT


def fetch_dashboard_trades(
    db: Session,
    *,
    manager_id: str,
    since: datetime | None,
    until: datetime,
    limit: int = DEFAULT_TRADE_LIMIT,
    ticker: str | None = None,
    action_filter: str = DEFAULT_TRADE_ACTION_FILTER,
    status_filter: str = DEFAULT_TRADE_STATUS_FILTER,
    mode_filter: str = DEFAULT_TRADE_MODE_FILTER,
    sort: str = DEFAULT_TRADE_SORT,
) -> list[Trade]:
    capped = min(max(limit, 1), MAX_TRADE_LIMIT)
    stmt = select(Trade).where(Trade.manager_id == manager_id).where(Trade.created_at <= until)
    if since is not None:
        stmt = stmt.where(Trade.created_at >= since)

    prefix = normalize_ticker_prefix(ticker)
    if prefix is not None:
        stmt = stmt.where(Trade.ticker.like(f"{prefix}%"))

    action_key = normalize_trade_action_filter(action_filter)
    if action_key == "buy":
        stmt = stmt.where(Trade.action == SignalAction.BUY)
    elif action_key == "sell":
        stmt = stmt.where(Trade.action == SignalAction.SELL)

    status_key = normalize_trade_status_filter(status_filter)
    if status_key == "failed":
        stmt = stmt.where(Trade.status.in_(FAILED_TRADE_STATUSES))
    elif status_key != "all":
        stmt = stmt.where(Trade.status == status_key)

    mode_key = normalize_trade_mode_filter(mode_filter)
    if mode_key == "live":
        stmt = stmt.where(Trade.simulation.is_(False))
    elif mode_key == "sim":
        stmt = stmt.where(Trade.simulation.is_(True))

    sort_key = normalize_trade_sort(sort)
    if sort_key == "oldest":
        stmt = stmt.order_by(Trade.created_at.asc())
    elif sort_key == "ticker_asc":
        stmt = stmt.order_by(Trade.ticker.asc(), Trade.created_at.desc())
    elif sort_key == "ticker_desc":
        stmt = stmt.order_by(Trade.ticker.desc(), Trade.created_at.desc())
    elif sort_key == "amount_desc":
        stmt = stmt.order_by(Trade.amount_usd.desc(), Trade.created_at.desc())
    else:
        stmt = stmt.order_by(Trade.created_at.desc())

    return list(db.execute(stmt.limit(capped)).scalars().all())
