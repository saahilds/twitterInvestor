from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.db_models import AccountSnapshot, SignalAction, Trade
from app.risk.market_hours import (
    extended_chart_bounds_for_date,
    filter_chart_session_points,
    is_within_extended_chart_hours,
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


def test_build_chart_includes_after_hours_snapshots() -> None:
    db = _session()
    morning = _et_today_at(11, 0)
    afterhours = _et_today_at(17, 30)
    late_night = _et_today_at(21, 0)

    db.add(
        Trade(
            parsed_signal_id=1,
            ticker="CRWV",
            action=SignalAction.BUY,
            amount_usd=1000.0,
            status="filled",
            simulation=False,
            created_at=morning,
            updated_at=morning,
        )
    )
    db.add(
        AccountSnapshot(
            account_number=None,
            recorded_at=morning,
            stocks_plus_cash=6800.0,
        )
    )
    db.add(
        AccountSnapshot(
            account_number=None,
            recorded_at=afterhours,
            stocks_plus_cash=6850.0,
        )
    )
    db.add(
        AccountSnapshot(
            account_number=None,
            recorded_at=late_night,
            stocks_plus_cash=6900.0,
        )
    )
    db.commit()

    points, annotations, source, window = portfolio_history.build_chart_series(
        db,
        range_key="1d",
        account_number=None,
        current_value=None,
        live_trades_only=False,
    )

    values = [point["v"] for point in points]
    assert 6850.0 in values
    assert 6900.0 not in values
    assert 6800.0 in values
    assert len(annotations) == 1
