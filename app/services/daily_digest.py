from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.db_models import DailyDigest, ParsedSignal, SignalAction, Tweet, utc_now
from app.risk.market_hours import (
    DigestPeriod,
    current_digest_period,
    digest_date_et,
    digests_day_bounds_utc,
    to_eastern,
)

_TRADE_ACTIONS = {SignalAction.BUY, SignalAction.SELL}


def _snippet(text: str, limit: int = 100) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def render_digest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"## Daily digest — {payload['digest_date']} (ET) · {payload['status']}"
        f" · through {payload.get('current_period', '')}",
        "",
        "### Trade alerts",
    ]
    alerts = payload.get("trade_alerts") or []
    if not alerts:
        lines.append("- (none)")
    else:
        for row in alerts:
            lines.append(f"- {row}")
    lines.append("")
    lines.append("### Trades executed")
    trades = payload.get("trades_executed") or []
    if not trades:
        lines.append("- (none)")
    else:
        for row in trades:
            lines.append(f"- {row}")
    lines.append("")
    stats = payload.get("stats") or {}
    lines.append(
        "### Stats\n"
        f"alerts={stats.get('alert_count', 0)} · executed={stats.get('trade_count', 0)} · "
        f"rejected={stats.get('rejected_count', 0)}"
    )
    return "\n".join(lines)


class DailyDigestService:
    """Rebuild digests from existing SQLite rows only (no X/RH/re-parse).

    Content is limited to trade alerts (BUY/SELL) and executed trades.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def get(self, digest_date: str | None = None) -> DailyDigest | None:
        date_key = digest_date or digest_date_et()
        with self.session_factory() as db:
            return db.execute(
                select(DailyDigest).where(DailyDigest.digest_date == date_key)
            ).scalar_one_or_none()

    def rebuild(
        self,
        digest_date: str | None = None,
        *,
        force: bool = False,
        now: datetime | None = None,
    ) -> DailyDigest | None:
        moment = now or datetime.now(timezone.utc)
        date_key = digest_date or digest_date_et(moment)
        start, end = digests_day_bounds_utc(date_key)

        with self.session_factory() as db:
            existing = db.execute(
                select(DailyDigest).where(DailyDigest.digest_date == date_key)
            ).scalar_one_or_none()
            if existing is not None and existing.status == "complete" and not force:
                return existing

            tweets = list(
                db.execute(
                    select(Tweet)
                    .options(selectinload(Tweet.parsed_signals).selectinload(ParsedSignal.trades))
                    .where(Tweet.posted_at >= start, Tweet.posted_at < end)
                    .order_by(Tweet.posted_at.asc())
                ).scalars().all()
            )

            trade_alerts: list[str] = []
            trades_executed: list[str] = []
            alert_count = 0
            trade_count = 0
            rejected_count = 0

            for tweet in tweets:
                trade_signals = [
                    signal
                    for signal in tweet.parsed_signals
                    if signal.action in _TRADE_ACTIONS
                ]
                if not trade_signals:
                    continue

                et = to_eastern(tweet.posted_at)
                time_label = et.strftime("%H:%M")
                snippet = _snippet(tweet.text, 80)

                for signal in trade_signals:
                    alert_count += 1
                    ticker = signal.ticker or "—"
                    trades = list(signal.trades)
                    if trades:
                        trade = trades[0]
                        trade_count += len(trades)
                        outcome = f"executed ${trade.amount_usd:.0f} ({trade.status})"
                        trades_executed.append(
                            f"{time_label} · {trade.action.value} {trade.ticker} "
                            f"${trade.amount_usd:.0f} · {trade.status}"
                            + (f" · sim" if trade.simulation else " · live")
                        )
                    elif signal.rejection_reason:
                        rejected_count += 1
                        outcome = f"blocked: {signal.rejection_reason}"
                    else:
                        outcome = "no trade"
                    trade_alerts.append(
                        f"{time_label} · {signal.action.value} {ticker} → {outcome}"
                        f" — \"{snippet}\""
                    )

            wall_period = current_digest_period(moment)
            payload = {
                "digest_date": date_key,
                "status": "complete" if wall_period == DigestPeriod.COMPLETE else "in_progress",
                "current_period": wall_period.value,
                "trade_alerts": trade_alerts,
                "trades_executed": trades_executed,
                # Keep legacy keys empty for older dashboard clients
                "trades_day": trades_executed,
                "rejected_day": [a for a in trade_alerts if "blocked:" in a],
                "informational_day": [],
                "periods": {},
                "stats": {
                    "alert_count": alert_count,
                    "trade_count": trade_count,
                    "rejected_count": rejected_count,
                    "tweet_count": alert_count,
                    "informational_count": 0,
                },
            }

            if existing is None:
                existing = DailyDigest(digest_date=date_key)
                db.add(existing)

            existing.status = payload["status"] if existing.status != "complete" or force else existing.status
            if force and wall_period == DigestPeriod.COMPLETE:
                existing.status = "complete"
                existing.completed_at = utc_now()
            existing.current_period = payload["current_period"]
            existing.periods_json = json.dumps(payload)
            existing.tweet_count = alert_count
            existing.trade_count = trade_count
            existing.rejected_count = rejected_count
            existing.informational_count = 0
            existing.last_rebuilt_at = utc_now()
            db.commit()
            db.refresh(existing)
            return existing

    def finalize(
        self,
        digest_date: str | None = None,
        *,
        now: datetime | None = None,
    ) -> DailyDigest | None:
        date_key = digest_date or digest_date_et(now)
        row = self.rebuild(date_key, force=True, now=now)
        if row is None:
            return None
        with self.session_factory() as db:
            entity = db.execute(
                select(DailyDigest).where(DailyDigest.digest_date == date_key)
            ).scalar_one_or_none()
            if entity is None:
                return None
            entity.status = "complete"
            entity.current_period = DigestPeriod.COMPLETE.value
            entity.completed_at = utc_now()
            try:
                payload = json.loads(entity.periods_json)
            except json.JSONDecodeError:
                payload = {}
            payload["status"] = "complete"
            payload["current_period"] = DigestPeriod.COMPLETE.value
            entity.periods_json = json.dumps(payload)
            db.commit()
            db.refresh(entity)
            return entity

    def mark_checkpoint_sent(self, digest_date: str, checkpoint: str) -> None:
        with self.session_factory() as db:
            entity = db.execute(
                select(DailyDigest).where(DailyDigest.digest_date == digest_date)
            ).scalar_one_or_none()
            if entity is None:
                return
            try:
                sent = json.loads(entity.webhook_checkpoints_json or "{}")
            except json.JSONDecodeError:
                sent = {}
            sent[checkpoint] = utc_now().isoformat()
            entity.webhook_checkpoints_json = json.dumps(sent)
            db.commit()

    def checkpoint_already_sent(self, digest_date: str, checkpoint: str) -> bool:
        with self.session_factory() as db:
            entity = db.execute(
                select(DailyDigest).where(DailyDigest.digest_date == digest_date)
            ).scalar_one_or_none()
            if entity is None:
                return False
            try:
                sent = json.loads(entity.webhook_checkpoints_json or "{}")
            except json.JSONDecodeError:
                return False
            return checkpoint in sent

    def to_api_dict(self, row: DailyDigest) -> dict[str, Any]:
        try:
            payload = json.loads(row.periods_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        stats = payload.get("stats") or {
            "alert_count": row.tweet_count,
            "trade_count": row.trade_count,
            "rejected_count": row.rejected_count,
            "tweet_count": row.tweet_count,
            "informational_count": 0,
        }
        return {
            "digest_date": row.digest_date,
            "status": row.status,
            "current_period": row.current_period,
            "summary_markdown": render_digest_markdown(payload) if payload else "",
            "periods": {},
            "trade_alerts": payload.get("trade_alerts") or [],
            "trades_executed": payload.get("trades_executed") or payload.get("trades_day") or [],
            "trades_day": payload.get("trades_executed") or payload.get("trades_day") or [],
            "rejected_day": payload.get("rejected_day") or [],
            "informational_day": [],
            "stats": stats,
            "last_rebuilt_at": row.last_rebuilt_at.isoformat() if row.last_rebuilt_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
