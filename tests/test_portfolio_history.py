from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.db_models import AccountSnapshot, SignalAction, Trade
from app.risk.market_hours import (
    chart_session_bounds_utc,
    extended_chart_bounds_for_date,
    filter_chart_session_points,
    is_within_extended_chart_hours,
    to_eastern,
)
from app.services import portfolio_history

ET = ZoneInfo("America/New_York")


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _et_today_at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now(ET).date()
    return datetime.combine(today, time(hour, minute), tzinfo=ET).astimezone(timezone.utc)


def test_filter_includes_after_hours_not_late_night() -> None:
    premarket = _et_today_at(8, 0)
    afterhours = _et_today_at(17, 0)
    late_night = _et_today_at(21, 0)
    filtered = filter_chart_session_points(
        [(premarket, 1000.0), (afterhours, 1010.0), (late_night, 1020.0)]
    )
    assert len(filtered) == 2
    assert is_within_extended_chart_hours(premarket)
    assert is_within_extended_chart_hours(afterhours)


def test_measured_series_uses_extended_session_window() -> None:
    open_utc, close_utc = extended_chart_bounds_for_date(datetime.now(ET).date())
    mid = open_utc + (close_utc - open_utc) / 2
    series = portfolio_history.build_measured_balance_series(
        [(mid, 6800.0), (mid + timedelta(minutes=30), 6820.0)],
        current_value=None,
        session_open=open_utc,
        session_end=close_utc,
    )
    assert len(series) >= 2
    for moment, _ in series:
        assert open_utc <= moment <= close_utc


def test_range_start_ytd_is_jan_1_et() -> None:
    now = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)
    start = portfolio_history.ytd_start_utc(now)
    start_et = start.astimezone(ET)
    assert start_et.year == 2026
    assert start_et.month == 1
    assert start_et.day == 1
    assert start_et.hour == 13


def test_prepend_chart_baseline_adds_starting_value() -> None:
    june = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    jan1 = portfolio_history.ytd_start_utc(june)
    series = portfolio_history._prepend_chart_baseline(
        [(june, 6500.0)],
        window_open=jan1,
        baseline_usd=5000.0,
    )
    assert len(series) == 2
    assert series[0][1] == 5000.0
    assert series[1][1] == 6500.0
    assert series[0][0] == jan1


def test_range_start_1m_is_rolling_thirty_days_not_month_to_date() -> None:
    now = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)
    start = portfolio_history.range_start("1m", now)
    assert start == now - timedelta(days=30)
    assert start.month == 5
    assert start.day == 10
    assert start != datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_resolve_window_1m_is_rolling_thirty_days() -> None:
    now = datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)
    start = portfolio_history.range_start("1m", now)
    session_open, session_end = chart_session_bounds_utc(now)
    window = portfolio_history.resolve_window(
        "1m",
        now=now,
        start=start,
        session_open=session_open,
        session_end=session_end,
        snapshot_pts=[],
    )
    assert window.window_start == start
    assert window.window_end == now


def test_compute_period_summary_positive_change() -> None:
    summary = portfolio_history.compute_period_summary(
        [
            (datetime(2026, 1, 1, tzinfo=timezone.utc), 5000.0),
            (datetime(2026, 6, 1, tzinfo=timezone.utc), 6000.0),
        ]
    )
    assert summary.current_value == 6000.0
    assert summary.period_start_value == 5000.0
    assert summary.change_usd == 1000.0
    assert summary.change_pct == 20.0


def test_compute_period_summary_zero_start() -> None:
    summary = portfolio_history.compute_period_summary(
        [(datetime(2026, 1, 1, tzinfo=timezone.utc), 0.0)]
    )
    assert summary.change_pct == 0.0


def test_aggregate_daily_chart_points_uses_last_reading_per_day() -> None:
    day1_am = _et_today_at(9, 0)
    day1_pm = _et_today_at(17, 0)
    yesterday = day1_am - timedelta(days=1)
    yesterday = yesterday.replace(hour=14, minute=0)
    points = [
        (yesterday, 6700.0),
        (day1_am, 6800.0),
        (day1_pm, 6850.0),
    ]
    aggregated = portfolio_history._aggregate_daily_chart_points(points)
    assert len(aggregated) == 2
    assert aggregated[0][1] == 6700.0
    assert aggregated[1][1] == 6850.0
    for moment, _ in aggregated:
        assert to_eastern(moment).hour == 13


def test_build_chart_all_range_uses_earliest_snapshot() -> None:
    db = _session()
    old = _et_today_at(11, 0) - timedelta(days=45)
    recent = _et_today_at(15, 0)
    db.add(
        AccountSnapshot(
            account_number=None,
            recorded_at=old,
            stocks_plus_cash=6000.0,
        )
    )
    db.add(
        AccountSnapshot(
            account_number=None,
            recorded_at=recent,
            stocks_plus_cash=6500.0,
        )
    )
    db.commit()

    _, _, _, window, summary = portfolio_history.build_chart_series(
        db,
        range_key="all",
        account_number=None,
        current_value=None,
        live_trades_only=False,
    )

    window_open = datetime.fromisoformat(window["window_start"])
    assert window_open <= old + timedelta(seconds=1)
    assert summary["current_value"] > 0


def test_build_chart_ytd_includes_summary_and_baseline() -> None:
    db = _session()
    june = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    db.add(
        AccountSnapshot(
            account_number=None,
            recorded_at=june,
            stocks_plus_cash=6500.0,
        )
    )
    db.commit()

    points, _, _, window, summary = portfolio_history.build_chart_series(
        db,
        range_key="ytd",
        account_number=None,
        current_value=None,
        live_trades_only=False,
        ytd_baseline_usd=5000.0,
    )

    window_open = datetime.fromisoformat(window["window_start"])
    expected_ytd = portfolio_history.ytd_start_utc(june)
    assert window_open == expected_ytd
    values = [p["v"] for p in points]
    assert 5000.0 in values
    assert summary["period_start_value"] == 5000.0


def test_annotations_include_late_trade_on_multi_day_range() -> None:
    db = _session()
    # 10pm ET two days ago (outside extended hours, still inside 1W window)
    late_night = _et_today_at(22, 0) - timedelta(days=2)
    trade = Trade(
        parsed_signal_id=1,
        ticker="NVDA",
        action=SignalAction.BUY,
        amount_usd=100.0,
        status="filled",
        simulation=False,
        created_at=late_night,
        updated_at=late_night,
    )
    db.add(trade)
    db.commit()

    now = datetime.now(timezone.utc)
    start = portfolio_history.range_start("1w", now)
    session_open, session_end = chart_session_bounds_utc(now)
    window = portfolio_history.resolve_window(
        "1w",
        now=now,
        start=start,
        session_open=session_open,
        session_end=session_end,
        snapshot_pts=[],
    )
    trades = portfolio_history.trade_annotations(db, start=start, end=now, live_trades_only=False)
    annotations = portfolio_history.build_trade_annotation_dicts(
        trades,
        window=window,
        range_key="1w",
    )
    assert len(annotations) == 1
    assert annotations[0]["ticker"] == "NVDA"


def test_annotations_exclude_late_trade_on_1d_range() -> None:
    db = _session()
    late_night = _et_today_at(22, 0)
    trade = Trade(
        parsed_signal_id=1,
        ticker="NVDA",
        action=SignalAction.BUY,
        amount_usd=100.0,
        status="filled",
        simulation=False,
        created_at=late_night,
        updated_at=late_night,
    )
    db.add(trade)
    db.commit()

    now = datetime.now(timezone.utc)
    start = portfolio_history.range_start("1d", now)
    session_open, session_end = chart_session_bounds_utc(now)
    window = portfolio_history.resolve_window(
        "1d",
        now=now,
        start=start,
        session_open=session_open,
        session_end=session_end,
        snapshot_pts=[],
    )
    trades = portfolio_history.trade_annotations(db, start=start, end=now, live_trades_only=False)
    annotations = portfolio_history.build_trade_annotation_dicts(
        trades,
        window=window,
        range_key="1d",
    )
    assert len(annotations) == 0


def test_build_chart_includes_after_hours_snapshots() -> None:
    morning = _et_today_at(11, 0)
    afterhours = _et_today_at(17, 30)
    late_night = _et_today_at(21, 0)
    open_utc, close_utc = extended_chart_bounds_for_date(datetime.now(ET).date())

    series = portfolio_history.build_measured_balance_series(
        [(morning, 6800.0), (afterhours, 6850.0), (late_night, 6900.0)],
        current_value=None,
        session_open=open_utc,
        session_end=close_utc,
    )
    values = [value for _, value in series]
    assert 6850.0 in values
    assert 6900.0 not in values
    assert 6800.0 in values
