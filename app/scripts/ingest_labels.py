"""Ingest truth labels from a weekly review JSONL into TweetLabel + training JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.models.db_models import SignalAction, TweetLabel
from app.parsing.training_examples import LabeledExample, append_labeled_examples


def ingest_review_rows(rows: list[dict], database_url: str) -> int:
    engine = create_engine(database_url)
    from app.db.base import Base
    from app.db.migrations import run_migrations
    from app.models import db_models as _db_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    SessionLocal = sessionmaker(bind=engine)
    examples: list[LabeledExample] = []
    saved = 0
    with SessionLocal() as db:
        for row in rows:
            action_raw = str(row.get("truth_action") or "").strip().upper()
            if not action_raw:
                continue
            try:
                action = SignalAction(action_raw)
            except ValueError:
                continue
            segment_text = str(row.get("segment_text") or "").strip()
            if not segment_text:
                continue
            tweet_id = str(row.get("tweet_id") or "").strip()
            ticker = row.get("ticker")
            ticker_s = str(ticker).upper() if ticker else None
            alloc = row.get("truth_allocation_pct")
            sell_frac = row.get("truth_sell_fraction")
            label = TweetLabel(
                tweet_id=tweet_id,
                ticker=ticker_s,
                action=action,
                segment_text=segment_text,
                allocation_pct=float(alloc) if alloc is not None and alloc != "" else None,
                sell_fraction=float(sell_frac) if sell_frac is not None and sell_frac != "" else None,
                labeled_by="human",
            )
            db.add(label)
            examples.append(
                LabeledExample(text=segment_text, action=action, source="reviewed")
            )
            saved += 1
        db.commit()
    append_labeled_examples(examples)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_file", type=Path, help="Path to review-YYYY-MM-DD.jsonl")
    args = parser.parse_args()

    settings = get_settings()
    rows: list[dict] = []
    with args.review_file.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    count = ingest_review_rows(rows, settings.database_url)
    print(f"Ingested {count} labeled rows from {args.review_file}")


if __name__ == "__main__":
    main()
