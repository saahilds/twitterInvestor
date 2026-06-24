from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import RecognizedTicker, utc_now


class RecognizedTickerRegistry:
    """Persistent set of tickers we have seen per account manager."""

    def is_recognized(self, ticker: str, db: Session, *, manager_id: str) -> bool:
        symbol = ticker.upper()
        row = db.get(RecognizedTicker, {"manager_id": manager_id, "ticker": symbol})
        return row is not None

    def register(
        self,
        ticker: str,
        db: Session,
        *,
        manager_id: str,
        source_tweet_id: str | None = None,
    ) -> None:
        symbol = ticker.upper()
        if db.get(RecognizedTicker, {"manager_id": manager_id, "ticker": symbol}) is not None:
            return
        db.add(
            RecognizedTicker(
                manager_id=manager_id,
                ticker=symbol,
                source_tweet_id=source_tweet_id,
                first_seen_at=utc_now(),
            )
        )
        db.commit()

    def seed(self, db: Session, tickers: set[str], *, manager_id: str) -> None:
        for ticker in sorted(tickers):
            symbol = ticker.upper()
            if db.get(RecognizedTicker, {"manager_id": manager_id, "ticker": symbol}) is not None:
                continue
            db.add(
                RecognizedTicker(
                    manager_id=manager_id,
                    ticker=symbol,
                    source_tweet_id=None,
                    first_seen_at=utc_now(),
                )
            )
        db.commit()

    def all_tickers(self, db: Session, *, manager_id: str) -> set[str]:
        rows = db.execute(
            select(RecognizedTicker.ticker).where(RecognizedTicker.manager_id == manager_id)
        ).scalars().all()
        return {str(ticker).upper() for ticker in rows}

    def union_tickers(self, db: Session, manager_ids: list[str]) -> set[str]:
        tickers: set[str] = set()
        for manager_id in manager_ids:
            tickers.update(self.all_tickers(db, manager_id=manager_id))
        return tickers
