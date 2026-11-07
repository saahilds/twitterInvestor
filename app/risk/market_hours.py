from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
# Extended window for balance chart / snapshots (earnings pre-market + after-hours).
EXTENDED_CHART_OPEN = time(7, 0)
EXTENDED_CHART_CLOSE = time(20, 0)
HALFTIME = time(12, 0)
FOURTH_QUARTER = time(15, 0)


class DigestPeriod(str, Enum):
    PREMARKET = "premarket"
    MORNING = "morning"
    HALFTIME = "halftime"
    FOURTH_QUARTER = "fourth_quarter"
    AFTER_HOURS = "after_hours"
    COMPLETE = "complete"


# Checkpoint events (not tweet buckets). market_close is a webhook/checkpoint only.
DIGEST_CHECKPOINTS: tuple[tuple[time, str], ...] = (
    (MARKET_OPEN, "premarket"),
    (HALFTIME, "halftime"),
    (FOURTH_QUARTER, "fourth_quarter"),
    (MARKET_CLOSE, "market_close"),
    (EXTENDED_CHART_CLOSE, "finalize"),
)


def digest_period_for(moment: datetime | None = None) -> DigestPeriod:
    """Bucket for a tweet/trade timestamp (market_close is not a bucket)."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    else:
        moment = to_eastern(moment)

    current = moment.time()
    if current < MARKET_OPEN:
        return DigestPeriod.PREMARKET
    if current < HALFTIME:
        return DigestPeriod.MORNING
    if current < FOURTH_QUARTER:
        return DigestPeriod.HALFTIME
    if current < MARKET_CLOSE:
        return DigestPeriod.FOURTH_QUARTER
    return DigestPeriod.AFTER_HOURS


def current_digest_period(moment: datetime | None = None) -> DigestPeriod:
    """Latest period that has started for wall-clock ``moment`` (Mon–Fri)."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    else:
        moment = to_eastern(moment)
    if moment.weekday() >= 5:
        return DigestPeriod.COMPLETE
    if moment.time() >= EXTENDED_CHART_CLOSE:
        return DigestPeriod.COMPLETE
    return digest_period_for(moment)


def digest_date_et(moment: datetime | None = None) -> str:
    if moment is None:
        moment = datetime.now(US_EASTERN)
    else:
        moment = to_eastern(moment)
    return moment.date().isoformat()


def digests_day_bounds_utc(digest_date: str) -> tuple[datetime, datetime]:
    day = datetime.fromisoformat(digest_date).date()
    start = datetime.combine(day, EXTENDED_CHART_OPEN, tzinfo=US_EASTERN).astimezone(timezone.utc)
    end = datetime.combine(day, EXTENDED_CHART_CLOSE, tzinfo=US_EASTERN).astimezone(timezone.utc)
    return start, end


def should_poll_x_feed(
    *,
    trading_window_enabled: bool,
    poll_only_during_market_hours: bool,
    moment: datetime | None = None,
) -> bool:
    """True when the worker should load X and look for new tweets."""
    if not trading_window_enabled or not poll_only_during_market_hours:
        return True
    return is_within_regular_market_hours(moment)


def seconds_until_regular_market_open(moment: datetime | None = None) -> float:
    """Seconds until the next weekday 9:30 ET. Zero while that session is open."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    else:
        moment = to_eastern(moment)
    if is_within_regular_market_hours(moment):
        return 0.0

    candidate = next_regular_session_open(moment)
    return max(0.0, (candidate - moment).total_seconds())


def next_regular_session_open(moment: datetime | None = None) -> datetime:
    """Next Mon–Fri 9:30 ET open at or after ``moment`` (returns current open if in session)."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    else:
        moment = to_eastern(moment)

    if is_within_regular_market_hours(moment):
        return datetime.combine(moment.date(), MARKET_OPEN, tzinfo=US_EASTERN)

    candidate = datetime.combine(moment.date(), MARKET_OPEN, tzinfo=US_EASTERN)
    if moment.weekday() >= 5 or moment.time() >= MARKET_OPEN:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def next_full_session_close_after(moment: datetime | None = None) -> datetime:
    """4:00 PM ET close of the next regular session that can still execute ``moment``.

    Premarket / open-session signals expire at that same day's close.
    After-hours / weekend signals expire at the following session's close.
    """
    open_et = next_regular_session_open(moment)
    close_et = datetime.combine(open_et.date(), MARKET_CLOSE, tzinfo=US_EASTERN)
    return close_et


def idle_sleep_seconds(
    *,
    poll_interval_seconds: int,
    feed_should_poll: bool,
    moment: datetime | None = None,
) -> float:
    """How long to sleep while the X tab is parked. Wakes at the open if that is sooner."""
    interval = float(poll_interval_seconds)
    if feed_should_poll:
        return interval
    until_open = seconds_until_regular_market_open(moment)
    if until_open <= 0:
        return interval
    return max(1.0, min(interval, until_open))


def is_within_regular_market_hours(moment: datetime | None = None) -> bool:
    """Return True when ``moment`` falls in US equity regular session (Mon–Fri 9:30–16:00 ET)."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=US_EASTERN)
    else:
        moment = moment.astimezone(US_EASTERN)

    if moment.weekday() >= 5:
        return False

    current = moment.time()
    return MARKET_OPEN <= current < MARKET_CLOSE


def us_trading_day_start_utc(moment: datetime | None = None) -> datetime:
    """UTC instant for 00:00 on the US/Eastern trading calendar date of ``moment``."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=US_EASTERN)
    else:
        moment = moment.astimezone(US_EASTERN)

    day_start_et = datetime.combine(moment.date(), time.min, tzinfo=US_EASTERN)
    return day_start_et.astimezone(timezone.utc)


def to_eastern(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=US_EASTERN)
    return moment.astimezone(US_EASTERN)


def is_regular_market_moment(moment: datetime) -> bool:
    """True when ``moment`` is Mon–Fri between 9:30 and 16:00 ET."""
    return is_within_regular_market_hours(moment)


def is_within_extended_chart_hours(moment: datetime | None = None) -> bool:
    """Mon–Fri 7:00–20:00 ET: pre-market + regular + after-hours (earnings moves)."""
    if moment is None:
        moment = datetime.now(US_EASTERN)
    else:
        moment = to_eastern(moment)

    if moment.weekday() >= 5:
        return False

    current = moment.time()
    return EXTENDED_CHART_OPEN <= current < EXTENDED_CHART_CLOSE


def regular_session_bounds_for_date(trading_date) -> tuple[datetime, datetime]:
    """9:30–16:00 ET on ``trading_date`` as UTC-aware datetimes."""
    open_et = datetime.combine(trading_date, MARKET_OPEN, tzinfo=US_EASTERN)
    close_et = datetime.combine(trading_date, MARKET_CLOSE, tzinfo=US_EASTERN)
    return open_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc)


def extended_chart_bounds_for_date(trading_date) -> tuple[datetime, datetime]:
    """7:00–20:00 ET on ``trading_date`` as UTC-aware datetimes."""
    open_et = datetime.combine(trading_date, EXTENDED_CHART_OPEN, tzinfo=US_EASTERN)
    close_et = datetime.combine(trading_date, EXTENDED_CHART_CLOSE, tzinfo=US_EASTERN)
    return open_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc)


# Backward-compatible alias for portfolio chart code.
session_bounds_for_date = extended_chart_bounds_for_date


def previous_trading_date(moment_et: datetime):
    day = moment_et.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def chart_session_bounds_utc(moment: datetime | None = None) -> tuple[datetime, datetime]:
    """UTC window for balance chart: 7:00–20:00 ET on the active or last trading day."""
    if moment is None:
        moment = datetime.now(timezone.utc)
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)

    moment_et = to_eastern(moment)
    if moment_et.weekday() >= 5:
        trading_date = previous_trading_date(moment_et)
    elif moment_et.time() < EXTENDED_CHART_OPEN:
        trading_date = previous_trading_date(moment_et)
    else:
        trading_date = moment_et.date()

    session_open, session_close = extended_chart_bounds_for_date(trading_date)
    if trading_date == moment_et.date() and is_within_extended_chart_hours(moment):
        session_end = min(moment, session_close)
    else:
        session_end = session_close
    return session_open, session_end


def filter_chart_session_points(
    points: list[tuple[datetime, float]],
) -> list[tuple[datetime, float]]:
    return [(moment, value) for moment, value in points if is_within_extended_chart_hours(moment)]


# Backward-compatible alias.
filter_market_hours_points = filter_chart_session_points
