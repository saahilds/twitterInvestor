"""Merge TweetLabel rows into the training set, retrain, and print metrics."""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config.settings import get_settings
from app.db.base import Base
from app.db.migrations import run_migrations
from app.models import db_models as _db_models  # noqa: F401
from app.models.db_models import TweetLabel
from app.parsing.evaluation import evaluate_holdout, suggested_thresholds
from app.parsing.ml_action_classifier import ActionClassifier
from app.parsing.training_examples import LabeledExample, append_labeled_examples, load_labeled_examples


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-calibrate",
        action="store_true",
        help="Skip probability calibration (faster / more stable for tiny datasets)",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    SessionLocal = sessionmaker(bind=engine)

    before = load_labeled_examples()
    before_report = evaluate_holdout(before)
    print("=== Before ===")
    print(f"examples={len(before)} holdout_macro_f1={before_report.macro_f1:.4f}")
    print(before_report.report_text)

    with SessionLocal() as db:
        labels = db.execute(select(TweetLabel).order_by(TweetLabel.created_at.asc())).scalars().all()
    reviewed = [
        LabeledExample(text=row.segment_text, action=row.action, source="reviewed")
        for row in labels
    ]
    added = append_labeled_examples(reviewed)
    after = load_labeled_examples()
    print(f"Merged {added} new reviewed labels (db had {len(reviewed)}; total examples={len(after)})")

    clf = ActionClassifier.train(after, calibrate=not args.no_calibrate)
    path = clf.save()
    after_report = evaluate_holdout(after)
    print("=== After ===")
    print(f"model={path} version={clf.version} holdout_macro_f1={after_report.macro_f1:.4f}")
    print(after_report.report_text)

    conf, margin = suggested_thresholds(after)
    print(f"Suggested SIGNAL_ML_MIN_CONFIDENCE={conf} SIGNAL_ML_MIN_MARGIN={margin}")
    print(
        f"(current settings: confidence={settings.signal_ml_min_confidence} "
        f"margin={settings.signal_ml_min_margin})"
    )


if __name__ == "__main__":
    main()
