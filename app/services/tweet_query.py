from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.db_models import ParsedSignal, SignalAction, Tweet
from app.services import portfolio_history

TWEET_RANGE_KEYS = portfolio_history.RANGE_KEYS
DEFAULT_TWEET_RANGE = "1w"
DEFAULT_TWEET_LIMIT = 500
MAX_TWEET_LIMIT = 2000
TWEET_SIGNAL_FILTERS = frozenset({"all", "alerts", "buy", "sell", "watch", "ignore"})
DEFAULT_TWEET_SIGNAL_FILTER = "all"


class TweetWindowError(ValueError):
    pass


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def resolve_tweet_window(
    *,
    range_key: str,
    since: datetime | None,
    until: datetime | None,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime]:
    """Return (since, until) for filtering tweets by posted_at."""
    moment = _as_utc(now or datetime.now(timezone.utc))

    if since is not None or until is not None:
        if since is None or until is None:
            raise TweetWindowError("custom_range_requires_since_and_until")
        since_dt = _as_utc(since)
        until_dt = _as_utc(until)
        if since_dt >= until_dt:
            raise TweetWindowError("since_must_be_before_until")
        return since_dt, until_dt

    key = range_key if range_key in TWEET_RANGE_KEYS else DEFAULT_TWEET_RANGE
    start = portfolio_history.range_start(key, moment)
    return start, moment


def _latest_signal_action_subquery():
    return (
        select(ParsedSignal.action)
        .where(ParsedSignal.tweet_pk == Tweet.id)
        .order_by(ParsedSignal.created_at.desc())
        .limit(1)
        .correlate(Tweet)
        .scalar_subquery()
    )


def normalize_signal_filter(signal_filter: str) -> str:
    return signal_filter if signal_filter in TWEET_SIGNAL_FILTERS else DEFAULT_TWEET_SIGNAL_FILTER


def apply_signal_filter(stmt, signal_filter: str):
    key = normalize_signal_filter(signal_filter)
    if key == "all":
        return stmt

    latest_action = _latest_signal_action_subquery()
    if key == "alerts":
        return stmt.where(latest_action.in_([SignalAction.BUY, SignalAction.SELL]))
    if key == "buy":
        return stmt.where(latest_action == SignalAction.BUY)
    if key == "sell":
        return stmt.where(latest_action == SignalAction.SELL)
    if key == "watch":
        return stmt.where(latest_action == SignalAction.WATCH)
    if key == "ignore":
        return stmt.where(latest_action == SignalAction.IGNORE)
    return stmt


def fetch_dashboard_tweets(
    db: Session,
    *,
    since: datetime | None,
    until: datetime,
    limit: int = DEFAULT_TWEET_LIMIT,
    signal_filter: str = DEFAULT_TWEET_SIGNAL_FILTER,
) -> list[Tweet]:
    capped = min(max(limit, 1), MAX_TWEET_LIMIT)
    stmt = (
        select(Tweet)
        .options(selectinload(Tweet.parsed_signals))
        .where(Tweet.posted_at <= until)
    )
    if since is not None:
        stmt = stmt.where(Tweet.posted_at >= since)
    stmt = apply_signal_filter(stmt, signal_filter)
    stmt = stmt.order_by(Tweet.posted_at.desc()).limit(capped)
    return list(db.execute(stmt).scalars().all())
