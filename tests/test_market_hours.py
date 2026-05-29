from datetime import datetime

from zoneinfo import ZoneInfo

from app.risk.market_hours import US_EASTERN, is_within_regular_market_hours

ET = US_EASTERN


def test_market_hours_weekday_open() -> None:
    moment = datetime(2026, 5, 20, 10, 0, tzinfo=ET)
    assert is_within_regular_market_hours(moment)


def test_market_hours_before_open() -> None:
    moment = datetime(2026, 5, 20, 9, 29, tzinfo=ET)
    assert not is_within_regular_market_hours(moment)


def test_market_hours_at_close() -> None:
    moment = datetime(2026, 5, 20, 16, 0, tzinfo=ET)
    assert not is_within_regular_market_hours(moment)


def test_market_hours_weekend() -> None:
    moment = datetime(2026, 5, 23, 12, 0, tzinfo=ET)
    assert not is_within_regular_market_hours(moment)
