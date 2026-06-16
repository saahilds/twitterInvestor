from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
# Extended window for balance chart / snapshots (earnings pre-market + after-hours).
EXTENDED_CHART_OPEN = time(7, 0)
EXTENDED_CHART_CLOSE = time(20, 0)


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
