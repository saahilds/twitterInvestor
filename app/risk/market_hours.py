from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


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
    from datetime import timezone

    if moment is None:
        moment = datetime.now(US_EASTERN)
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=US_EASTERN)
    else:
        moment = moment.astimezone(US_EASTERN)

    day_start_et = datetime.combine(moment.date(), time.min, tzinfo=US_EASTERN)
    return day_start_et.astimezone(timezone.utc)
