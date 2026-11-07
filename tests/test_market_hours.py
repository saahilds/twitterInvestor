from datetime import datetime

from zoneinfo import ZoneInfo

from app.risk.market_hours import (
    US_EASTERN,
    extended_chart_bounds_for_date,
    idle_sleep_seconds,
    is_within_extended_chart_hours,
    is_within_regular_market_hours,
    next_full_session_close_after,
    next_regular_session_open,
    seconds_until_regular_market_open,
    should_poll_x_feed,
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


def test_x_feed_polls_only_during_regular_session() -> None:
    closed = datetime(2026, 5, 20, 8, 0, tzinfo=ET)
    open_ = datetime(2026, 5, 20, 10, 0, tzinfo=ET)
    assert not should_poll_x_feed(
        trading_window_enabled=True,
        poll_only_during_market_hours=True,
        moment=closed,
    )
    assert should_poll_x_feed(
        trading_window_enabled=True,
        poll_only_during_market_hours=True,
        moment=open_,
    )
    assert should_poll_x_feed(
        trading_window_enabled=True,
        poll_only_during_market_hours=False,
        moment=closed,
    )
    assert should_poll_x_feed(
        trading_window_enabled=False,
        poll_only_during_market_hours=True,
        moment=closed,
    )


def test_idle_sleep_wakes_at_the_open() -> None:
    before_open = datetime(2026, 5, 20, 9, 29, 30, tzinfo=ET)
    assert seconds_until_regular_market_open(before_open) == 30
    assert (
        idle_sleep_seconds(
            poll_interval_seconds=60,
            feed_should_poll=False,
            moment=before_open,
        )
        == 30
    )
    overnight = datetime(2026, 5, 20, 21, 0, tzinfo=ET)
    assert (
        idle_sleep_seconds(
            poll_interval_seconds=60,
            feed_should_poll=False,
            moment=overnight,
        )
        == 60
    )


def test_extended_bounds_span_premarket_to_afterhours() -> None:
    open_utc, close_utc = extended_chart_bounds_for_date(datetime(2026, 5, 20, tzinfo=ET).date())
    open_et = open_utc.astimezone(ET)
    close_et = close_utc.astimezone(ET)
    assert open_et.hour == 7 and open_et.minute == 0
    assert close_et.hour == 20 and close_et.minute == 0


def test_next_session_open_and_full_day_close() -> None:
    premarket = datetime(2026, 5, 20, 8, 0, tzinfo=ET)
    assert next_regular_session_open(premarket) == datetime(2026, 5, 20, 9, 30, tzinfo=ET)
    assert next_full_session_close_after(premarket) == datetime(2026, 5, 20, 16, 0, tzinfo=ET)

    after_close = datetime(2026, 5, 20, 17, 0, tzinfo=ET)
    assert next_regular_session_open(after_close) == datetime(2026, 5, 21, 9, 30, tzinfo=ET)
    assert next_full_session_close_after(after_close) == datetime(2026, 5, 21, 16, 0, tzinfo=ET)

    friday_night = datetime(2026, 5, 22, 18, 0, tzinfo=ET)
    assert next_regular_session_open(friday_night) == datetime(2026, 5, 25, 9, 30, tzinfo=ET)
    assert next_full_session_close_after(friday_night) == datetime(2026, 5, 25, 16, 0, tzinfo=ET)
