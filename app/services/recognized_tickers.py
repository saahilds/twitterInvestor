from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import RecognizedTicker, utc_now


class RecognizedTickerRegistry:
    """Persistent set of tickers we have seen; grows on first successful introduction."""

    def is_recognized(self, ticker: str, db: Session) -> bool:
        symbol = ticker.upper()
        row = db.get(RecognizedTicker, symbol)
        return row is not None

    def register(self, ticker: str, db: Session, *, source_tweet_id: str | None = None) -> None:
        symbol = ticker.upper()
        if db.get(RecognizedTicker, symbol) is not None:
            return
        db.add(
            RecognizedTicker(
                ticker=symbol,
                source_tweet_id=source_tweet_id,
                first_seen_at=utc_now(),
            )
        )
        db.commit()

    def seed(self, db: Session, tickers: set[str]) -> None:
        for ticker in sorted(tickers):
            symbol = ticker.upper()
            if db.get(RecognizedTicker, symbol) is not None:
                continue
            db.add(
                RecognizedTicker(
                    ticker=symbol,
                    source_tweet_id=None,
                    first_seen_at=utc_now(),
                )
            )
        db.commit()

    def all_tickers(self, db: Session) -> set[str]:
        rows = db.execute(select(RecognizedTicker.ticker)).scalars().all()
        return {str(ticker).upper() for ticker in rows}
