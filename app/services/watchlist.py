from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.db_models import WatchlistEntry, utc_now
from app.parsing.watch_conviction import WatchConviction, conviction_score_delta


class WatchlistRegistry:
    """Per-manager speculative interest registry with conviction scoring."""

    def __init__(self, *, max_conviction_score: float = 5.0, stale_days: int = 30) -> None:
        self.max_conviction_score = max_conviction_score
        self.stale_days = stale_days

    def get(self, ticker: str, db: Session, *, manager_id: str) -> WatchlistEntry | None:
        symbol = ticker.upper()
        return db.get(WatchlistEntry, {"manager_id": manager_id, "ticker": symbol})

    def upsert(
        self,
        ticker: str,
        db: Session,
        *,
        manager_id: str,
        watch_conviction: WatchConviction,
        source_tweet_id: str | None = None,
    ) -> WatchlistEntry:
        symbol = ticker.upper()
        now = utc_now()
        delta = conviction_score_delta(watch_conviction)
        row = db.get(WatchlistEntry, {"manager_id": manager_id, "ticker": symbol})
        if row is None:
            row = WatchlistEntry(
                manager_id=manager_id,
                ticker=symbol,
                conviction_score=min(self.max_conviction_score, delta),
                last_seen_at=now,
                source_tweet_id=source_tweet_id,
                watch_conviction=watch_conviction.value,
            )
            db.add(row)
        else:
            row.conviction_score = min(self.max_conviction_score, row.conviction_score + delta)
            row.last_seen_at = now
            row.source_tweet_id = source_tweet_id
            row.watch_conviction = watch_conviction.value
        db.commit()
        db.refresh(row)
        return row

    def prune_stale(self, db: Session, manager_ids: list[str]) -> int:
        if not manager_ids or self.stale_days <= 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.stale_days)
        result = db.execute(
            delete(WatchlistEntry).where(
                WatchlistEntry.manager_id.in_(manager_ids),
                WatchlistEntry.last_seen_at < cutoff,
            )
        )
        db.commit()
        return int(result.rowcount or 0)

    def all_entries(self, db: Session, *, manager_id: str) -> list[WatchlistEntry]:
        rows = db.execute(
            select(WatchlistEntry)
            .where(WatchlistEntry.manager_id == manager_id)
            .order_by(WatchlistEntry.conviction_score.desc(), WatchlistEntry.last_seen_at.desc())
        ).scalars().all()
        return list(rows)

    def union_tickers(self, db: Session, manager_ids: list[str]) -> set[str]:
        tickers: set[str] = set()
        for manager_id in manager_ids:
            rows = db.execute(
                select(WatchlistEntry.ticker).where(WatchlistEntry.manager_id == manager_id)
            ).scalars().all()
            tickers.update(str(ticker).upper() for ticker in rows)
        return tickers
