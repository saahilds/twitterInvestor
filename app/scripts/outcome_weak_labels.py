"""Optional: derive weak labels from realized trade P&L outcomes."""

from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.db.base import Base
from app.db.migrations import run_migrations
from app.models import db_models as _db_models  # noqa: F401
from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.parsing.training_examples import LabeledExample, append_labeled_examples


def build_outcome_weak_labels(db) -> list[LabeledExample]:
    """Map filled trades back to tweet text as weak BUY/SELL prior labels.

    Ignores simulation-only and failed trades. Does not invent IGNORE/WATCH —
    those still require human review.
    """
    trades = db.execute(
        select(Trade, ParsedSignal)
        .join(ParsedSignal, Trade.parsed_signal_id == ParsedSignal.id)
        .where(Trade.simulation.is_(False))
        .where(Trade.status.in_(["filled", "partially_filled", "executed"]))
        .where(Trade.action.in_([SignalAction.BUY, SignalAction.SELL]))
    ).all()

    by_text: dict[tuple[str, str], list[float]] = defaultdict(list)
    for trade, signal in trades:
        text = (signal.raw_text or "").strip()
        if not text:
            continue
        key = (text, trade.action.value)
        by_text[key].append(float(trade.fill_price or 0.0))

    examples: list[LabeledExample] = []
    for (text, action_value), fills in by_text.items():
        if not fills:
            continue
        examples.append(
            LabeledExample(
                text=text,
                action=SignalAction(action_value),
                source="outcome_weak",
            )
        )
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append weak outcome labels into data/labeled_signals.jsonl",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as db:
        examples = build_outcome_weak_labels(db)

    print(f"Derived {len(examples)} weak outcome labels")
    for ex in examples[:20]:
        preview = " ".join(ex.text.split())[:120]
        print(f"  {ex.action.value}: {preview}")
    if args.apply and examples:
        added = append_labeled_examples(examples)
        print(f"Appended {added} new weak labels")


if __name__ == "__main__":
    main()
