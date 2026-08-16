from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.db_models import ParsedSignal, SignalAction, Trade, Tweet
from app.parsing.ticker_mentions import ticker_text_match_clause
from app.services import portfolio_history

TWEET_RANGE_KEYS = portfolio_history.RANGE_KEYS
DEFAULT_TWEET_RANGE = "1w"
DEFAULT_TWEET_LIMIT = 500
MAX_TWEET_LIMIT = 2000
TWEET_SIGNAL_FILTERS = frozenset({"all", "alerts", "buy", "sell", "watch", "ignore"})
DEFAULT_TWEET_SIGNAL_FILTER = "all"
TWEET_TRADED_FILTERS = frozenset({"all", "traded", "not_traded"})
DEFAULT_TWEET_TRADED_FILTER = "all"
TWEET_SORT_KEYS = frozenset(
    {"newest", "oldest", "ticker_asc", "ticker_desc", "confidence_desc"}
)
DEFAULT_TWEET_SORT = "newest"


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


def _latest_signal_column(column):
    return (
        select(column)
        .where(ParsedSignal.tweet_pk == Tweet.id)
        .order_by(ParsedSignal.created_at.desc())
        .limit(1)
        .correlate(Tweet)
        .scalar_subquery()
    )


def _latest_signal_action_subquery():
    return _latest_signal_column(ParsedSignal.action)


def _latest_signal_ticker_subquery():
    return _latest_signal_column(ParsedSignal.ticker)


def _latest_signal_confidence_subquery():
    return _latest_signal_column(ParsedSignal.confidence)


def normalize_signal_filter(signal_filter: str) -> str:
    return signal_filter if signal_filter in TWEET_SIGNAL_FILTERS else DEFAULT_TWEET_SIGNAL_FILTER


def normalize_traded_filter(traded_filter: str | None) -> str:
    key = (traded_filter or DEFAULT_TWEET_TRADED_FILTER).strip().lower()
    return key if key in TWEET_TRADED_FILTERS else DEFAULT_TWEET_TRADED_FILTER


def normalize_tweet_sort(sort: str | None) -> str:
    key = (sort or DEFAULT_TWEET_SORT).strip().lower()
    return key if key in TWEET_SORT_KEYS else DEFAULT_TWEET_SORT


def normalize_ticker_prefix(ticker: str | None) -> str | None:
    if ticker is None:
        return None
    text = ticker.strip().upper()
    return text or None


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


def apply_ticker_filter(stmt, ticker: str | None):
    prefix = normalize_ticker_prefix(ticker)
    if prefix is None:
        return stmt

    any_signal_ticker = exists(
        select(ParsedSignal.id)
        .where(ParsedSignal.tweet_pk == Tweet.id)
        .where(ParsedSignal.ticker.is_not(None))
        .where(ParsedSignal.ticker.like(f"{prefix}%"))
    )
    return stmt.where(or_(any_signal_ticker, ticker_text_match_clause(Tweet.text, prefix)))


def apply_traded_filter(stmt, traded_filter: str | None):
    key = normalize_traded_filter(traded_filter)
    if key == "all":
        return stmt
    has_trade = exists(
        select(Trade.id)
        .join(ParsedSignal, Trade.parsed_signal_id == ParsedSignal.id)
        .where(ParsedSignal.tweet_pk == Tweet.id)
    )
    if key == "traded":
        return stmt.where(has_trade)
    return stmt.where(~has_trade)


def apply_tweet_sort(stmt, sort: str | None):
    key = normalize_tweet_sort(sort)
    if key == "oldest":
        return stmt.order_by(Tweet.posted_at.asc())
    if key == "ticker_asc":
        return stmt.order_by(
            _latest_signal_ticker_subquery().asc().nulls_last(),
            Tweet.posted_at.desc(),
        )
    if key == "ticker_desc":
        return stmt.order_by(
            _latest_signal_ticker_subquery().desc().nulls_last(),
            Tweet.posted_at.desc(),
        )
    if key == "confidence_desc":
        return stmt.order_by(
            _latest_signal_confidence_subquery().desc().nulls_last(),
            Tweet.posted_at.desc(),
        )
    return stmt.order_by(Tweet.posted_at.desc())


def fetch_dashboard_tweets(
    db: Session,
    *,
    since: datetime | None,
    until: datetime,
    limit: int = DEFAULT_TWEET_LIMIT,
    signal_filter: str = DEFAULT_TWEET_SIGNAL_FILTER,
    ticker: str | None = None,
    traded_filter: str = DEFAULT_TWEET_TRADED_FILTER,
    sort: str = DEFAULT_TWEET_SORT,
) -> list[Tweet]:
    capped = min(max(limit, 1), MAX_TWEET_LIMIT)
    stmt = (
        select(Tweet)
        .options(selectinload(Tweet.parsed_signals).selectinload(ParsedSignal.trades))
        .where(Tweet.posted_at <= until)
    )
    if since is not None:
        stmt = stmt.where(Tweet.posted_at >= since)
    stmt = apply_signal_filter(stmt, signal_filter)
    stmt = apply_ticker_filter(stmt, ticker)
    stmt = apply_traded_filter(stmt, traded_filter)
    stmt = apply_tweet_sort(stmt, sort).limit(capped)
    return list(db.execute(stmt).scalars().all())
