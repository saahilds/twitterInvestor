from datetime import datetime

from zoneinfo import ZoneInfo

from app.risk.market_hours import (
    US_EASTERN,
    extended_chart_bounds_for_date,
    is_within_extended_chart_hours,
    is_within_regular_market_hours,
)

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


def test_extended_chart_premarket() -> None:
    moment = datetime(2026, 5, 20, 8, 0, tzinfo=ET)
    assert is_within_extended_chart_hours(moment)
    assert not is_within_regular_market_hours(moment)


def test_extended_chart_after_hours() -> None:
    moment = datetime(2026, 5, 20, 17, 30, tzinfo=ET)
    assert is_within_extended_chart_hours(moment)
    assert not is_within_regular_market_hours(moment)


def test_extended_chart_overnight_excluded() -> None:
    moment = datetime(2026, 5, 20, 21, 0, tzinfo=ET)
    assert not is_within_extended_chart_hours(moment)


def test_extended_bounds_span_premarket_to_afterhours() -> None:
    open_utc, close_utc = extended_chart_bounds_for_date(datetime(2026, 5, 20, tzinfo=ET).date())
    open_et = open_utc.astimezone(ET)
    close_et = close_utc.astimezone(ET)
    assert open_et.hour == 7 and open_et.minute == 0
    assert close_et.hour == 20 and close_et.minute == 0
