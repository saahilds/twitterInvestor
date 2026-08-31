from __future__ import annotations

from dataclasses import dataclass

from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split

from app.models.db_models import SignalAction
from app.parsing.ml_action_classifier import ActionClassifier
from app.parsing.text_normalize import normalize_for_action_model
from app.parsing.training_examples import LabeledExample, load_labeled_examples


@dataclass(frozen=True, slots=True)
class EvalReport:
    labels: list[str]
    y_true: list[str]
    y_pred: list[str]
    report_text: str
    macro_f1: float
    confusion: list[list[int]]
    support: int


def evaluate_holdout(
    examples: tuple[LabeledExample, ...] | None = None,
    *,
    test_size: float = 0.25,
    random_state: int = 42,
) -> EvalReport:
    dataset = list(examples if examples is not None else load_labeled_examples())
    labels = [ex.action.value for ex in dataset]
    unique = sorted(set(labels))
    if len(unique) < 2 or len(dataset) < 8:
        clf = ActionClassifier.train(tuple(dataset), calibrate=False)
        y_true = [ex.action.value for ex in dataset]
        y_pred = [clf.predict(ex.text).action.value for ex in dataset]
        return _build_report(y_true, y_pred, unique)

    try:
        train_ex, test_ex = train_test_split(
            dataset,
            test_size=test_size,
            random_state=random_state,
            stratify=labels,
        )
    except ValueError:
        train_ex, test_ex = train_test_split(
            dataset,
            test_size=test_size,
            random_state=random_state,
        )

    clf = ActionClassifier.train(tuple(train_ex), calibrate=False)
    y_true = [ex.action.value for ex in test_ex]
    y_pred = [clf.predict(ex.text).action.value for ex in test_ex]
    return _build_report(y_true, y_pred, unique)


def evaluate_cross_val(
    examples: tuple[LabeledExample, ...] | None = None,
    *,
    n_splits: int = 3,
) -> EvalReport:
    dataset = examples if examples is not None else load_labeled_examples()
    texts = [normalize_for_action_model(ex.text) for ex in dataset]
    labels = [ex.action.value for ex in dataset]
    unique = sorted(set(labels))
    min_class = min(labels.count(label) for label in unique)
    folds = max(2, min(n_splits, min_class))
    if folds < 2 or len(dataset) < folds * 2:
        return evaluate_holdout(dataset)

    clf = ActionClassifier.train(dataset, calibrate=False)
    y_pred = cross_val_predict(
        clf._pipeline,
        texts,
        labels,
        cv=StratifiedKFold(n_splits=folds, shuffle=True, random_state=42),
    )
    return _build_report(labels, list(y_pred), unique)


def sweep_thresholds(
    examples: tuple[LabeledExample, ...] | None = None,
    *,
    confidences: tuple[float, ...] = (0.30, 0.35, 0.40, 0.42, 0.45, 0.50, 0.55),
    margins: tuple[float, ...] = (0.04, 0.06, 0.08, 0.10, 0.12),
    held_out: bool = True,
) -> list[dict[str, float | int]]:
    """Sweep ML usability gates on a held-out slice when possible."""
    dataset = list(examples if examples is not None else load_labeled_examples())
    labels = [ex.action.value for ex in dataset]
    train_ex, test_ex = dataset, dataset
    if held_out and len(dataset) >= 12:
        try:
            train_ex, test_ex = train_test_split(
                dataset,
                test_size=0.3,
                random_state=42,
                stratify=labels,
            )
        except ValueError:
            train_ex, test_ex = train_test_split(
                dataset,
                test_size=0.3,
                random_state=42,
            )

    clf = ActionClassifier.train(tuple(train_ex), calibrate=False)
    rows: list[dict[str, float | int]] = []
    for conf in confidences:
        for margin in margins:
            y_true: list[str] = []
            y_pred: list[str] = []
            for ex in test_ex:
                pred = clf.predict(ex.text)
                usable = (
                    pred.action.value != "IGNORE"
                    and pred.confidence >= conf
                    and pred.margin >= margin
                )
                predicted = pred.action.value if usable else "IGNORE"
                y_true.append(ex.action.value)
                y_pred.append(predicted)
            macro = float(f1_score(y_true, y_pred, average="macro", zero_division=0))
            rows.append(
                {
                    "ml_min_confidence": conf,
                    "ml_min_margin": margin,
                    "macro_f1": round(macro, 4),
                    "support": len(test_ex),
                }
            )
    rows.sort(key=lambda row: float(row["macro_f1"]), reverse=True)
    return rows


def suggested_thresholds(
    examples: tuple[LabeledExample, ...] | None = None,
) -> tuple[float, float]:
    rows = sweep_thresholds(examples)
    if not rows:
        return 0.42, 0.08
    best = rows[0]
    return float(best["ml_min_confidence"]), float(best["ml_min_margin"])


def _build_report(y_true: list[str], y_pred: list[str], labels: list[str]) -> EvalReport:
    report = classification_report(y_true, y_pred, labels=labels, zero_division=0)
    matrix = confusion_matrix(y_true, y_pred, labels=labels).tolist()
    macro = float(f1_score(y_true, y_pred, average="macro", zero_division=0, labels=labels))
    return EvalReport(
        labels=labels,
        y_true=y_true,
        y_pred=y_pred,
        report_text=report,
        macro_f1=macro,
        confusion=matrix,
        support=len(y_true),
    )


# Re-export for typing convenience
__all__ = [
    "EvalReport",
    "evaluate_holdout",
    "evaluate_cross_val",
    "sweep_thresholds",
    "suggested_thresholds",
    "SignalAction",
]
