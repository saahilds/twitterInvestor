"""Replay Trade-header tweets from the DB into a review JSONL for human labeling.

Prefer real CK Trade alerts over curated toy snippets. After filling truth_action,
ingest with:

    uv run python -m app.scripts.ingest_labels data/reviews/trade-replay-YYYY-MM-DD.jsonl

Retrain only once you have substantial segment volume:

    uv run python -m app.scripts.retrain_from_labels
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text as sql_text

from app.config.settings import get_settings
from app.models.db_models import SignalAction
from app.parsing.factory import build_signal_parser
from app.parsing.ml_action_classifier import ActionClassifier
from app.parsing.trade_header import has_trade_header
from app.parsing.word_forms import keyword_match_pattern

REVIEWS_DIR = Path(__file__).resolve().parents[2] / "data" / "reviews"

_SELL_FAMILY_STEMS = ("trim", "cut", "sell", "downsize", "reduce")
_BUY_FAMILY_STEMS = ("add", "buy", "upsize")
_SELL_PHRASE_HINTS = ("freeing up", "from ")
_BUY_PHRASE_HINTS = ("starter", "% port", "port in")


def _flag_reasons(signal_action: str, text: str, *, needs_review: bool, review_reason: str | None) -> list[str]:
    reasons: list[str] = []
    lower = text.lower()
    if needs_review or review_reason == "trade_header_low_conf":
        reasons.append(review_reason or "trade_header_low_conf")
    sell_family = any(keyword_match_pattern(stem).search(lower) for stem in _SELL_FAMILY_STEMS) or any(
        hint in lower for hint in _SELL_PHRASE_HINTS
    )
    buy_family = any(keyword_match_pattern(stem).search(lower) for stem in _BUY_FAMILY_STEMS) or any(
        hint in lower for hint in _BUY_PHRASE_HINTS
    )
    if signal_action == SignalAction.BUY.value and sell_family:
        reasons.append("buy_with_sell_family_language")
    if signal_action == SignalAction.SELL.value and buy_family:
        past_entry = any(
            phrase in lower
            for phrase in ("added at", "bought at", "added these", "i added", "we added")
        )
        if not past_entry:
            reasons.append("sell_with_buy_family_language")
    if signal_action in {SignalAction.BUY.value, SignalAction.SELL.value}:
        reasons.append("trade_header_actionable")
    return reasons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="Max review rows to emit")
    parser.add_argument(
        "--all-tweets",
        action="store_true",
        help="Include non-Trade-header tweets (default: Trade header only)",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)
    hybrid = build_signal_parser(settings)
    classifier = ActionClassifier.load() or ActionClassifier.train()

    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                """
                SELECT t.tweet_id, t.posted_at, t.text
                FROM tweets t
                ORDER BY t.posted_at DESC
                LIMIT 2000
                """
            )
        ).fetchall()

    review_rows: list[dict] = []
    trade_header_count = 0
    for tweet_id, posted_at, text in rows:
        if not args.all_tweets and not has_trade_header(text):
            continue
        trade_header_count += 1
        signals = hybrid.parse(text, str(tweet_id))
        actions = {s.action for s in signals}
        mixed = SignalAction.BUY in actions and SignalAction.SELL in actions

        for signal in signals:
            reasons = _flag_reasons(
                signal.action.value,
                signal.raw_text or text,
                needs_review=signal.needs_review,
                review_reason=signal.review_reason,
            )
            if mixed:
                reasons.append("mixed_actions_same_tweet")
            # Always queue Trade-header rows so humans can confirm segment labels.
            if not reasons and not has_trade_header(text):
                continue
            if not reasons:
                reasons.append("trade_header_confirm")

            ml = classifier.predict(signal.raw_text or text)
            review_rows.append(
                {
                    "tweet_id": str(tweet_id),
                    "posted_at": (
                        posted_at.isoformat() if hasattr(posted_at, "isoformat") else str(posted_at)
                    ),
                    "ticker": signal.ticker,
                    "segment_text": signal.raw_text or text,
                    "model_action": signal.action.value,
                    "model_confidence": round(float(signal.confidence), 4),
                    "ml_action": ml.action.value,
                    "ml_confidence": round(ml.confidence, 4),
                    "ml_margin": round(ml.margin, 4),
                    "reason_flagged": "; ".join(dict.fromkeys(reasons)),
                    "truth_action": "",
                    "truth_allocation_pct": None,
                    "truth_sell_fraction": None,
                }
            )
            if len(review_rows) >= args.limit:
                break
        if len(review_rows) >= args.limit:
            break

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = REVIEWS_DIR / f"trade-replay-{stamp}.jsonl"
    with out_path.open("w") as handle:
        for row in review_rows:
            handle.write(json.dumps(row) + "\n")

    print(
        f"Queued {len(review_rows)} review rows "
        f"(scanned {trade_header_count} Trade-header tweets) → {out_path}\n"
    )
    print(
        "Next: fill truth_action (BUY/SELL/WATCH/IGNORE), then:\n"
        f"  uv run python -m app.scripts.ingest_labels {out_path}\n"
        "Retrain only after hundreds of diverse real segments:\n"
        "  uv run python -m app.scripts.retrain_from_labels\n"
    )
    for idx, row in enumerate(review_rows[:15], 1):
        preview = " ".join(str(row["segment_text"]).split())
        if len(preview) > 180:
            preview = preview[:177] + "..."
        print(
            f"{idx}. [{row['tweet_id']}] {row['ticker']} "
            f"model={row['model_action']} ({row['reason_flagged']})"
        )
        print(f"   {preview}")


if __name__ == "__main__":
    main()
