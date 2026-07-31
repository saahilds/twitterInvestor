from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.db_models import ParserFeedback, SignalAction, utc_now
from app.parsing.training_examples import LabeledExample


def _export_jsonl(rows: list[ParserFeedback], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "tweet_id": row.tweet_id,
                        "text": row.tweet_text,
                        "parser_action": row.parser_action.value,
                        "parser_ticker": row.parser_ticker,
                        "correct_action": row.correct_action.value,
                        "note": row.note,
                    }
                )
                + "\n"
            )
    return len(rows)


def _apply_to_training(rows: list[ParserFeedback]) -> int:
    """Append LabeledExample lines for unexported feedback (simple append before closing paren)."""
    path = Path("app/parsing/training_examples.py")
    text = path.read_text(encoding="utf-8")
    marker = ")\n"
    # Find end of TRAINING_EXAMPLES tuple
    idx = text.rfind("TRAINING_EXAMPLES")
    if idx < 0:
        raise RuntimeError("TRAINING_EXAMPLES not found")
    end = text.find("\n)", idx)
    if end < 0:
        raise RuntimeError("Could not locate end of TRAINING_EXAMPLES")
    additions: list[str] = []
    for row in rows:
        if row.correct_action == SignalAction.WATCH:
            continue
        example = LabeledExample(row.tweet_text, row.correct_action)
        escaped = example.text.replace("\\", "\\\\").replace('"', '\\"')
        additions.append(
            f'    LabeledExample("{escaped}", SignalAction.{example.action.value}),\n'
        )
    if not additions:
        return 0
    insert_at = end
    updated = text[:insert_at] + "\n" + "".join(additions) + text[insert_at:]
    path.write_text(updated, encoding="utf-8")
    return len(additions)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export parser feedback for training review.")
    parser.add_argument("--out", default="data/feedback.jsonl")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append unexported rows to training_examples.py and mark exported_at",
    )
    parser.add_argument("--include-exported", action="store_true")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        stmt = select(ParserFeedback).order_by(ParserFeedback.created_at.asc())
        if not args.include_exported:
            stmt = stmt.where(ParserFeedback.exported_at.is_(None))
        rows = list(db.execute(stmt).scalars().all())
        count = _export_jsonl(rows, Path(args.out))
        print(f"Wrote {count} rows to {args.out}")
        if args.apply and rows:
            applied = _apply_to_training(rows)
            now = utc_now()
            for row in rows:
                row.exported_at = now
                db.add(row)
            db.commit()
            print(f"Applied {applied} examples to training_examples.py")


if __name__ == "__main__":
    main()
