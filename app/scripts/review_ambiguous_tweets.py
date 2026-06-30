"""List tweets from the DB that need human buy/sell/ignore labels."""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, text as sql_text

from app.config.settings import get_settings
from app.models.db_models import SignalAction
from app.parsing.hybrid_signal_parser import HybridSignalParser


def _has_ticker(text: str, tickers: list[str]) -> bool:
    if "$" in text:
        return True
    upper = text.upper()
    return any(ticker in upper for ticker in tickers)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=30, help="Max ambiguous tweets to print")
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)
    hybrid = HybridSignalParser(
        known_tickers=settings.allowed_tickers,
        default_trade_size_usd=settings.default_trade_size_usd,
        default_sell_fraction=settings.default_sell_fraction,
        ml_min_confidence=settings.signal_ml_min_confidence,
        ml_min_margin=settings.signal_ml_min_margin,
    )

    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                """
                SELECT t.tweet_id, t.posted_at, t.text
                FROM tweets t
                LEFT JOIN parsed_signals ps ON ps.tweet_pk = t.id
                WHERE ps.id IS NULL
                ORDER BY t.posted_at DESC
                LIMIT 500
                """
            )
        ).fetchall()

    ambiguous: list[tuple] = []
    for tweet_id, posted_at, text in rows:
        if not _has_ticker(text, settings.allowed_tickers):
            continue
        signal = hybrid.parse(text, tweet_id)
        ml = hybrid._classifier.predict(text)
        reasons: list[str] = []
        if signal.action == SignalAction.IGNORE and ml.action != SignalAction.IGNORE:
            reasons.append(f"parser=IGNORE ml={ml.action.value}({ml.confidence:.2f})")
        elif signal.action != SignalAction.IGNORE and ml.action != signal.action and ml.confidence > 0.4:
            reasons.append(f"conflict parser={signal.action.value} ml={ml.action.value}")
        elif ml.margin < 0.12 and ml.confidence < 0.55:
            reasons.append(f"low_ml margin={ml.margin:.2f} conf={ml.confidence:.2f}")
        if not reasons:
            continue
        ambiguous.append((tweet_id, posted_at, text, "; ".join(reasons), signal.action.value, ml.action.value))

    print(f"Found {len(ambiguous)} ambiguous tweets (showing up to {args.limit}):\n")
    for idx, (tweet_id, posted_at, text, reason, parser_action, ml_action) in enumerate(ambiguous[: args.limit], 1):
        preview = " ".join(text.split())
        if len(preview) > 280:
            preview = preview[:277] + "..."
        print(f"{idx}. [{tweet_id}] {posted_at}")
        print(f"   parser={parser_action}  ml={ml_action}  ({reason})")
        print(f"   {preview}")
        print(f"   → Label as: BUY / SELL / WATCH / IGNORE ?")
        print()


if __name__ == "__main__":
    main()
