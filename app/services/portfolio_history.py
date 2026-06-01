from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import AccountSnapshot, SignalAction, Trade
from app.risk.market_hours import (
    chart_session_bounds_utc,
    filter_chart_session_points,
    is_within_extended_chart_hours,
)

SNAPSHOT_MIN_INTERVAL_SECONDS = 300
FAILED_TRADE_STATUSES = frozenset({"failed", "rejected", "cancelled", "canceled"})
IMPORTANT_TRADE_STATUSES = frozenset(
    {"submitted", "filled", "executed", "confirmed", "partially_filled", "simulated"}
)
EXECUTED_TRADE_STATUSES = frozenset({"filled", "executed", "confirmed"})

RANGE_KEYS = frozenset({"1d", "1w", "1m", "3m", "ytd", "all"})


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


RANGE_DAYS: dict[str, int | None] = {
    "1d": 1,
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "ytd": None,
    "all": None,
}


def range_start(range_key: str, moment: datetime) -> datetime | None:
    if range_key not in RANGE_KEYS:
        range_key = "1w"
    if range_key == "all":
        return None
    if range_key == "ytd":
        return datetime(moment.year, 1, 1, tzinfo=timezone.utc)
    days = RANGE_DAYS[range_key]
    assert days is not None
    return moment - timedelta(days=days)


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
) -> list[Trade]:
    end_utc = _as_utc(end)
    start_utc = _as_utc(start) if start is not None else None
    stmt = select(Trade).where(Trade.created_at <= end).order_by(Trade.created_at.asc())
    if start_utc is not None:
        stmt = stmt.where(Trade.created_at >= start)
    if live_trades_only:
        stmt = stmt.where(Trade.simulation.is_(False))

    rows = db.execute(stmt).scalars().all()
    return [row for row in rows if is_executed_trade(row)]


def build_chart_series(
    db: Session,
    *,
    range_key: str,
    account_number: str | None,
    current_value: float | None,
    live_trades_only: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]], str, dict[str, str | None]]:
    """Chart values are measured ``stocks_plus_cash`` during 7am–8pm ET (extended session)."""
    if range_key not in RANGE_KEYS:
        range_key = "1w"

    now = datetime.now(timezone.utc)
    start = range_start(range_key, now)
    session_open, session_end = chart_session_bounds_utc(now)
    if range_key == "1d":
        window_open, window_end = session_open, session_end
    else:
        window_open = start if start is not None else session_open
        window_end = now

    snapshot_pts = _snapshot_points(
        db,
        account_number=account_number,
        start=start,
        end=now,
    )
    live_value = current_value if is_within_extended_chart_hours(now) else None
    series = build_measured_balance_series(
        snapshot_pts,
        current_value=live_value,
        session_end=window_end,
        session_open=window_open,
    )

    if len(series) >= 2:
        source = "snapshots"
    elif len(series) == 1:
        source = "live_only" if live_value is not None else "limited"
    else:
        source = "limited"

    points = [{"t": moment.isoformat(), "v": round(value, 2)} for moment, value in series]

    annotations: list[dict[str, object]] = []
    window_open_utc = _as_utc(window_open)
    window_end_utc = _as_utc(window_end)
    for trade in trade_annotations(db, start=start, end=now, live_trades_only=live_trades_only):
        trade_at = _as_utc(trade.created_at)
        if trade_at < window_open_utc or trade_at > window_end_utc:
            continue
        if not is_within_extended_chart_hours(trade_at):
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

    chart_x_min = session_open if range_key == "1d" else window_open
    chart_x_max = session_end if range_key == "1d" else window_end
    window = {
        "session_open": chart_x_min.isoformat(),
        "session_end": chart_x_max.isoformat(),
    }
    return points, annotations, source, window
