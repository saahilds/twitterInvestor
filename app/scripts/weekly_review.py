"""Weekly ambiguous / low-confidence tweet review for human labeling."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text as sql_text

from app.config.settings import get_settings
from app.models.db_models import SignalAction
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.ml_action_classifier import ActionClassifier
from app.parsing.signal_segments import segment_trade_units

REVIEWS_DIR = Path(__file__).resolve().parents[2] / "data" / "reviews"


def _parse_since(value: str) -> timedelta:
    raw = value.strip().lower()
    if raw.endswith("d"):
        return timedelta(days=int(raw[:-1] or "7"))
    if raw.endswith("h"):
        return timedelta(hours=int(raw[:-1] or "24"))
    return timedelta(days=int(raw))


def _flag_reasons(
    *,
    model_action: SignalAction,
    rule_action: SignalAction,
    ml_confidence: float,
    ml_margin: float,
    ml_min_confidence: float,
    ml_min_margin: float,
    segment_count: int,
    has_trade_verbs: bool,
    needs_review: bool = False,
    review_reason: str | None = None,
) -> list[str]:
    reasons: list[str] = []
    if needs_review or review_reason == "trade_header_low_conf":
        reasons.append(review_reason or "trade_header_low_conf")
    if rule_action != model_action and model_action != SignalAction.IGNORE:
        reasons.append(f"conflict rule={rule_action.value} ml={model_action.value}")
    if rule_action != SignalAction.IGNORE and model_action != rule_action and ml_confidence > 0.4:
        reasons.append(f"conflict parser={rule_action.value} ml={model_action.value}")
    near_conf = ml_min_confidence <= ml_confidence < ml_min_confidence + 0.12
    near_margin = ml_margin < ml_min_margin + 0.06
    if near_conf or near_margin:
        reasons.append(f"low_ml conf={ml_confidence:.2f} margin={ml_margin:.2f}")
    if segment_count > 1:
        reasons.append(f"multi_ticker segments={segment_count}")
    if rule_action == SignalAction.IGNORE and has_trade_verbs:
        reasons.append("ignore_with_trade_verbs")
    return reasons


_TRADE_VERBS = (
    "adding",
    "added",
    "trimming",
    "trimmed",
    "sold",
    "selling",
    "bought",
    "entered",
    "closed",
    "upsized",
    "upsize",
    "upsizing",
    "downsized",
    "downsize",
    "downsizing",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="7d", help="Lookback window (e.g. 7d, 24h)")
    parser.add_argument("--limit", type=int, default=25, help="Max review rows to emit")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for truth_action on each row and write TweetLabel rows",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)
    classifier = ActionClassifier.load() or ActionClassifier.train()
    hybrid = HybridSignalParser(
        known_tickers=settings.known_tickers,
        default_sell_fraction=settings.default_sell_fraction,
        action_classifier=classifier,
        ml_min_confidence=settings.signal_ml_min_confidence,
        ml_min_margin=settings.signal_ml_min_margin,
    )

    since = datetime.now(timezone.utc) - _parse_since(args.since)
    with engine.connect() as conn:
        rows = conn.execute(
            sql_text(
                """
                SELECT t.tweet_id, t.posted_at, t.text
                FROM tweets t
                WHERE t.posted_at >= :since
                ORDER BY t.posted_at DESC
                LIMIT 1000
                """
            ),
            {"since": since},
        ).fetchall()

    review_rows: list[dict] = []
    for tweet_id, posted_at, text in rows:
        segments = segment_trade_units(text) or []
        signals = hybrid.parse(text, tweet_id)
        if not segments and signals:
            segments_payload = [
                {
                    "ticker": signals[0].ticker,
                    "segment_text": text,
                    "model_action": signals[0].action.value,
                    "model_confidence": signals[0].confidence,
                    "needs_review": signals[0].needs_review,
                    "review_reason": signals[0].review_reason,
                }
            ]
        else:
            segments_payload = []
            for segment, signal in zip(segments, signals):
                segments_payload.append(
                    {
                        "ticker": segment.ticker,
                        "segment_text": segment.local_text,
                        "model_action": signal.action.value,
                        "model_confidence": signal.confidence,
                        "needs_review": signal.needs_review,
                        "review_reason": signal.review_reason,
                    }
                )
            # Zip can truncate if lengths differ — fall back to parse results.
            if len(segments) != len(signals):
                segments_payload = [
                    {
                        "ticker": signal.ticker,
                        "segment_text": signal.raw_text,
                        "model_action": signal.action.value,
                        "model_confidence": signal.confidence,
                        "needs_review": signal.needs_review,
                        "review_reason": signal.review_reason,
                    }
                    for signal in signals
                ]

        lower = text.lower()
        has_trade_verbs = any(verb in lower for verb in _TRADE_VERBS)
        for payload in segments_payload:
            ml = classifier.predict(payload["segment_text"])
            rule_signals = hybrid._rules.parse(payload["segment_text"], tweet_id)
            rule_action = rule_signals[0].action if rule_signals else SignalAction.IGNORE
            reasons = _flag_reasons(
                model_action=SignalAction(payload["model_action"]),
                rule_action=rule_action,
                ml_confidence=ml.confidence,
                ml_margin=ml.margin,
                ml_min_confidence=settings.signal_ml_min_confidence,
                ml_min_margin=settings.signal_ml_min_margin,
                segment_count=max(len(segments), 1),
                has_trade_verbs=has_trade_verbs,
                needs_review=bool(payload.get("needs_review")),
                review_reason=payload.get("review_reason"),
            )
            if not reasons and payload["model_action"] != SignalAction.IGNORE.value:
                # Clear actionable high-confidence signal — skip review.
                continue
            if not reasons:
                continue
            review_rows.append(
                {
                    "tweet_id": tweet_id,
                    "posted_at": posted_at.isoformat() if hasattr(posted_at, "isoformat") else str(posted_at),
                    "ticker": payload["ticker"],
                    "segment_text": payload["segment_text"],
                    "model_action": payload["model_action"],
                    "model_confidence": round(float(payload["model_confidence"]), 4),
                    "ml_action": ml.action.value,
                    "ml_confidence": round(ml.confidence, 4),
                    "ml_margin": round(ml.margin, 4),
                    "reason_flagged": "; ".join(reasons),
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
    out_path = REVIEWS_DIR / f"review-{stamp}.jsonl"
    with out_path.open("w") as handle:
        for row in review_rows:
            handle.write(json.dumps(row) + "\n")

    print(f"Queued {len(review_rows)} review rows (since {args.since}) → {out_path}\n")
    for idx, row in enumerate(review_rows, 1):
        preview = " ".join(row["segment_text"].split())
        if len(preview) > 220:
            preview = preview[:217] + "..."
        print(f"{idx}. [{row['tweet_id']}] {row['posted_at']}")
        print(
            f"   ticker={row['ticker']} model={row['model_action']} "
            f"ml={row['ml_action']}({row['ml_confidence']:.2f})  ({row['reason_flagged']})"
        )
        print(f"   {preview}")
        print("   → Fill truth_action: BUY / SELL / WATCH / IGNORE")
        print()

    if args.interactive and review_rows:
        from app.scripts.ingest_labels import ingest_review_rows

        for row in review_rows:
            preview = " ".join(row["segment_text"].split())[:200]
            print(f"\n{preview}")
            print(f"model={row['model_action']} reason={row['reason_flagged']}")
            raw = input("truth_action [BUY/SELL/WATCH/IGNORE/skip]: ").strip().upper()
            if raw in {"BUY", "SELL", "WATCH", "IGNORE"}:
                row["truth_action"] = raw
            elif not raw or raw == "SKIP":
                continue
            else:
                print("skipped (unknown label)")
                continue
        labeled = [row for row in review_rows if row.get("truth_action")]
        with out_path.open("w") as handle:
            for row in review_rows:
                handle.write(json.dumps(row) + "\n")
        if labeled:
            count = ingest_review_rows(labeled, settings.database_url)
            print(f"Saved {count} labels from interactive review.")


if __name__ == "__main__":
    main()
