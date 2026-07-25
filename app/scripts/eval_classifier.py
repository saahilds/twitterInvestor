"""Evaluate the action classifier and optionally sweep ML thresholds."""

from __future__ import annotations

import argparse
import json

from app.parsing.evaluation import evaluate_cross_val, evaluate_holdout, sweep_thresholds
from app.parsing.training_examples import load_labeled_examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cv", action="store_true", help="Use stratified cross-validation")
    parser.add_argument("--sweep", action="store_true", help="Sweep confidence/margin thresholds")
    parser.add_argument("--top", type=int, default=10, help="Rows to show from threshold sweep")
    args = parser.parse_args()

    examples = load_labeled_examples()
    print(f"Loaded {len(examples)} labeled examples")

    report = evaluate_cross_val(examples) if args.cv else evaluate_holdout(examples)
    print(f"macro_f1={report.macro_f1:.4f} support={report.support}")
    print(report.report_text)
    print("confusion:", json.dumps({"labels": report.labels, "matrix": report.confusion}))

    if args.sweep:
        rows = sweep_thresholds(examples)[: args.top]
        print("\nTop threshold configs:")
        for row in rows:
            print(
                f"  conf={row['ml_min_confidence']:.2f} margin={row['ml_min_margin']:.2f} "
                f"macro_f1={row['macro_f1']}"
            )


if __name__ == "__main__":
    main()
