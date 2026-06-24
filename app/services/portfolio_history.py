from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import AccountSnapshot, SignalAction, Trade
from app.risk.market_hours import (
    US_EASTERN,
    chart_session_bounds_utc,
    filter_chart_session_points,
    is_within_extended_chart_hours,
    to_eastern,
)

SNAPSHOT_MIN_INTERVAL_SECONDS = 300
FAILED_TRADE_STATUSES = frozenset({"failed", "rejected", "cancelled", "canceled"})
IMPORTANT_TRADE_STATUSES = frozenset(
    {"submitted", "filled", "executed", "confirmed", "partially_filled", "simulated"}
)
EXECUTED_TRADE_STATUSES = frozenset({"filled", "executed", "confirmed"})

RANGE_KEYS = frozenset({"1d", "1w", "1m", "3m", "ytd", "all"})
DEFAULT_YTD_BASELINE_USD = 5000.0

RANGE_DAYS: dict[str, int | None] = {
    "1d": 1,
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "ytd": None,
    "all": None,
}


@dataclass(slots=True)
class ChartWindow:
    window_start: datetime
    window_end: datetime


@dataclass(slots=True)
class PeriodSummary:
    current_value: float
    period_start_value: float
    change_usd: float
    change_pct: float

    def as_dict(self) -> dict[str, float]:
        return {
            "current_value": round(self.current_value, 2),
            "period_start_value": round(self.period_start_value, 2),
            "change_usd": round(self.change_usd, 2),
            "change_pct": round(self.change_pct, 4),
        }


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def ytd_start_utc(moment: datetime) -> datetime:
    """Jan 1 1pm ET for the chart year (matches daily point anchors)."""
    year = to_eastern(_as_utc(moment)).year
    anchor = datetime.combine(date(year, 1, 1), time(13, 0), tzinfo=US_EASTERN)
    return anchor.astimezone(timezone.utc)


def range_start(range_key: str, moment: datetime) -> datetime | None:
    """UTC start instant for a chart range (rolling lookback, except YTD/all)."""
    if range_key not in RANGE_KEYS:
        range_key = "1w"
    if range_key == "all":
        return None
    if range_key == "ytd":
        return ytd_start_utc(moment)
    days = RANGE_DAYS[range_key]
    assert days is not None
    return _as_utc(moment) - timedelta(days=days)


def is_executed_trade(trade: Trade) -> bool:
    status = (trade.status or "").lower()
    if status not in EXECUTED_TRADE_STATUSES:
        return False
    return trade.action in (SignalAction.BUY, SignalAction.SELL)


def is_important_trade(trade: Trade) -> bool:
    status = (trade.status or "").lower()
    if status in FAILED_TRADE_STATUSES:
        return False
    if trade.action not in (SignalAction.BUY, SignalAction.SELL):
        return False
    if status in IMPORTANT_TRADE_STATUSES:
        return True
    return status not in FAILED_TRADE_STATUSES


def record_snapshot(
    db: Session,
    *,
    account_number: str | None,
    stocks_plus_cash: float | None,
    holdings_market_value: float | None,
    cash: float | None,
) -> None:
    """Persist measured account balance during extended hours (7am–8pm ET)."""
    if stocks_plus_cash is None:
        return

    now = datetime.now(timezone.utc)
    if not is_within_extended_chart_hours(now):
        return
    stmt = (
        select(AccountSnapshot)
        .where(AccountSnapshot.account_number == account_number)
        .order_by(AccountSnapshot.recorded_at.desc())
        .limit(1)
    )
    last = db.execute(stmt).scalars().first()
    if last is not None:
        age = (now - _as_utc(last.recorded_at)).total_seconds()
        if age < SNAPSHOT_MIN_INTERVAL_SECONDS and abs(last.stocks_plus_cash - stocks_plus_cash) < 0.01:
            return

    db.add(
        AccountSnapshot(
            account_number=account_number,
            recorded_at=now,
            stocks_plus_cash=stocks_plus_cash,
            holdings_market_value=holdings_market_value,
            cash=cash,
        )
    )
    db.commit()


def latest_snapshot_value(
    db: Session,
    *,
    account_number: str | None,
) -> float | None:
    stmt = select(AccountSnapshot).order_by(AccountSnapshot.recorded_at.desc()).limit(1)
    if account_number is not None:
        stmt = stmt.where(AccountSnapshot.account_number == account_number)
    row = db.execute(stmt).scalars().first()
    return row.stocks_plus_cash if row is not None else None


def _snapshot_points(
    db: Session,
    *,
    account_number: str | None,
    start: datetime | None,
    end: datetime,
) -> list[tuple[datetime, float]]:
    stmt = (
        select(AccountSnapshot)
        .where(AccountSnapshot.recorded_at <= end)
        .order_by(AccountSnapshot.recorded_at.asc())
    )
    if account_number is not None:
        stmt = stmt.where(AccountSnapshot.account_number == account_number)
    if start is not None:
        stmt = stmt.where(AccountSnapshot.recorded_at >= start)

    rows = db.execute(stmt).scalars().all()
    return [(_as_utc(row.recorded_at), row.stocks_plus_cash) for row in rows]


def _dedupe_snapshot_series(
    points: list[tuple[datetime, float]],
) -> list[tuple[datetime, float]]:
    deduped: dict[int, tuple[datetime, float]] = {}
    for moment, value in points:
        deduped[int(_as_utc(moment).timestamp())] = (_as_utc(moment), value)
    return [deduped[key] for key in sorted(deduped)]


def _aggregate_daily_chart_points(
    points: list[tuple[datetime, float]],
) -> list[tuple[datetime, float]]:
    """Collapse intraday snapshots to one point per ET day (last reading), anchored at 1pm ET."""
    by_day: dict[date, tuple[datetime, float]] = {}
    for moment, value in points:
        day = to_eastern(_as_utc(moment)).date()
        prev = by_day.get(day)
        if prev is None or _as_utc(moment) >= _as_utc(prev[0]):
            by_day[day] = (_as_utc(moment), value)

    aggregated: list[tuple[datetime, float]] = []
    for day in sorted(by_day):
        _, value = by_day[day]
        anchor = datetime.combine(day, time(13, 0), tzinfo=US_EASTERN).astimezone(timezone.utc)
        aggregated.append((anchor, value))
    return aggregated


def resolve_window(
    range_key: str,
    *,
    now: datetime,
    start: datetime | None,
    session_open: datetime,
    session_end: datetime,
    snapshot_pts: list[tuple[datetime, float]],
) -> ChartWindow:
    """Single source of truth for chart x-axis bounds."""
    if range_key == "1d":
        return ChartWindow(window_start=_as_utc(session_open), window_end=_as_utc(session_end))
    if range_key == "all":
        if snapshot_pts:
            return ChartWindow(window_start=_as_utc(snapshot_pts[0][0]), window_end=_as_utc(now))
        return ChartWindow(window_start=_as_utc(session_open), window_end=_as_utc(now))
    assert start is not None
    return ChartWindow(window_start=_as_utc(start), window_end=_as_utc(now))


def resample_points(
    series: list[tuple[datetime, float]],
    range_key: str,
) -> list[tuple[datetime, float]]:
    if range_key != "1d" and len(series) > 1:
        return _aggregate_daily_chart_points(series)
    return series


def apply_baseline(
    series: list[tuple[datetime, float]],
    *,
    range_key: str,
    window_open: datetime,
    baseline_usd: float,
) -> list[tuple[datetime, float]]:
    if range_key == "ytd":
        return _prepend_chart_baseline(
            series,
            window_open=window_open,
            baseline_usd=baseline_usd,
        )
    return series


def _prepend_chart_baseline(
    series: list[tuple[datetime, float]],
    *,
    window_open: datetime,
    baseline_usd: float,
) -> list[tuple[datetime, float]]:
    """Assume ``baseline_usd`` at ``window_open`` when history starts later."""
    anchor = _as_utc(window_open)
    if not series:
        return [(anchor, baseline_usd)]
    first_moment = _as_utc(series[0][0])
    if first_moment <= anchor + timedelta(hours=24):
        return series
    return [(anchor, baseline_usd), *series]


def compute_period_summary(
    series: list[tuple[datetime, float]],
    *,
    live_value: float | None = None,
) -> PeriodSummary:
    if not series:
        value = live_value if live_value is not None else 0.0
        return PeriodSummary(
            current_value=value,
            period_start_value=value,
            change_usd=0.0,
            change_pct=0.0,
        )

    period_start = series[0][1]
    current = live_value if live_value is not None else series[-1][1]
    change_usd = current - period_start
    if period_start != 0:
        change_pct = (change_usd / period_start) * 100.0
    else:
        change_pct = 0.0 if change_usd == 0 else 100.0
    return PeriodSummary(
        current_value=current,
        period_start_value=period_start,
        change_usd=change_usd,
        change_pct=change_pct,
    )


def build_measured_balance_series(
    snapshot_pts: list[tuple[datetime, float]],
    *,
    current_value: float | None,
    session_end: datetime,
    session_open: datetime,
) -> list[tuple[datetime, float]]:
    """Balance line from in-session snapshots (7am–8pm ET, incl. pre/after-hours)."""
    session_open_utc = _as_utc(session_open)
    session_end_utc = _as_utc(session_end)
    in_session = [
        (moment, value)
        for moment, value in snapshot_pts
        if session_open_utc <= _as_utc(moment) <= session_end_utc
    ]
    series = _dedupe_snapshot_series(filter_chart_session_points(in_session))

    if current_value is not None and is_within_extended_chart_hours(session_end):
        if not series or _as_utc(series[-1][0]) < session_end_utc - timedelta(seconds=30):
            series.append((session_end_utc, current_value))
        elif abs(series[-1][1] - current_value) >= 0.01:
            series[-1] = (session_end_utc, current_value)

    if len(series) == 1:
        only_moment, only_value = series[0]
        if _as_utc(only_moment) < session_end_utc - timedelta(seconds=60):
            series.append(
                (
                    session_end_utc,
                    current_value if current_value is not None else only_value,
                )
            )

    if not series and current_value is not None:
        series = [(session_end_utc, current_value)]

    return series


def trade_annotations(
    db: Session,
    *,
    start: datetime | None,
    end: datetime,
    live_trades_only: bool,
    manager_id: str | None = None,
) -> list[Trade]:
    end_utc = _as_utc(end)
    start_utc = _as_utc(start) if start is not None else None
    stmt = select(Trade).where(Trade.created_at <= end).order_by(Trade.created_at.asc())
    if start_utc is not None:
        stmt = stmt.where(Trade.created_at >= start)
    if live_trades_only:
        stmt = stmt.where(Trade.simulation.is_(False))
    if manager_id is not None:
        stmt = stmt.where(Trade.manager_id == manager_id)

    rows = db.execute(stmt).scalars().all()
    return [row for row in rows if is_executed_trade(row)]


def build_trade_annotation_dicts(
    trades: list[Trade],
    *,
    window: ChartWindow,
    range_key: str,
) -> list[dict[str, object]]:
    annotations: list[dict[str, object]] = []
    window_start = _as_utc(window.window_start)
    window_end = _as_utc(window.window_end)
    filter_extended_hours = range_key == "1d"

    for trade in trades:
        trade_at = _as_utc(trade.created_at)
        if trade_at < window_start or trade_at > window_end:
            continue
        if filter_extended_hours and not is_within_extended_chart_hours(trade_at):
            continue
        action = trade.action.value
        sim = " (sim)" if trade.simulation else ""
        annotations.append(
            {
                "trade_id": trade.id,
                "t": trade_at.isoformat(),
                "ticker": trade.ticker,
                "action": action,
                "amount_usd": trade.amount_usd,
                "status": trade.status,
                "simulation": trade.simulation,
                "label": f"{action} {trade.ticker} ${trade.amount_usd:.0f}{sim}",
            }
        )
    return annotations


def _series_source(
    series: list[tuple[datetime, float]],
    *,
    live_value: float | None,
) -> str:
    if len(series) >= 2:
        return "snapshots"
    if len(series) == 1:
        return "live_only" if live_value is not None else "limited"
    return "limited"


def build_chart_series(
    db: Session,
    *,
    range_key: str,
    account_number: str | None,
    current_value: float | None,
    live_trades_only: bool,
    ytd_baseline_usd: float = DEFAULT_YTD_BASELINE_USD,
    manager_id: str | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str, dict[str, str | None], dict[str, float]]:
    """Chart values are measured ``stocks_plus_cash`` during 7am–8pm ET (extended session)."""
    if range_key not in RANGE_KEYS:
        range_key = "1w"

    now = datetime.now(timezone.utc)
    start = range_start(range_key, now)
    session_open, session_end = chart_session_bounds_utc(now)
    snapshot_pts = _snapshot_points(
        db,
        account_number=account_number,
        start=start,
        end=now,
    )
    chart_window = resolve_window(
        range_key,
        now=now,
        start=start,
        session_open=session_open,
        session_end=session_end,
        snapshot_pts=snapshot_pts,
    )

    live_value = current_value if is_within_extended_chart_hours(now) else None
    series = build_measured_balance_series(
        snapshot_pts,
        current_value=live_value,
        session_end=chart_window.window_end,
        session_open=chart_window.window_start,
    )
    series = resample_points(series, range_key)
    series = apply_baseline(
        series,
        range_key=range_key,
        window_open=chart_window.window_start,
        baseline_usd=ytd_baseline_usd,
    )

    summary = compute_period_summary(series, live_value=live_value)
    source = _series_source(series, live_value=live_value)
    points = [{"t": moment.isoformat(), "v": round(value, 2)} for moment, value in series]

    trades = trade_annotations(
        db,
        start=start,
        end=now,
        live_trades_only=live_trades_only,
        manager_id=manager_id,
    )
    annotations = build_trade_annotation_dicts(
        trades,
        window=chart_window,
        range_key=range_key,
    )

    window_start_iso = chart_window.window_start.isoformat()
    window_end_iso = chart_window.window_end.isoformat()
    window = {
        "window_start": window_start_iso,
        "window_end": window_end_iso,
        "session_open": window_start_iso,
        "session_end": window_end_iso,
    }
    return points, annotations, source, window, summary.as_dict()
