from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import joblib
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from app.models.db_models import SignalAction
from app.parsing.text_normalize import normalize_for_action_model
from app.parsing.training_examples import LabeledExample, load_labeled_examples

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "models"


@dataclass(frozen=True, slots=True)
class ActionPrediction:
    action: SignalAction
    confidence: float
    margin: float


class ActionClassifier:
    """Buy/sell/watch/ignore classifier (word+char TF-IDF + calibrated LR)."""

    def __init__(self, pipeline: Pipeline, *, version: str = "in-memory") -> None:
        self._pipeline = pipeline
        self.version = version

    @classmethod
    def train(
        cls,
        examples: tuple[LabeledExample, ...] | None = None,
        *,
        calibrate: bool = True,
    ) -> ActionClassifier:
        dataset = examples if examples is not None else load_labeled_examples()
        texts = [normalize_for_action_model(example.text) for example in dataset]
        labels = [example.action.value for example in dataset]
        version = examples_hash(dataset)

        base = LogisticRegression(
            max_iter=2_000,
            class_weight="balanced",
        )
        clf: object = base
        if calibrate and len(set(labels)) >= 2 and len(labels) >= 12:
            # Sigmoid calibration with small folds suitable for ~70 examples.
            n_folds = min(3, min(labels.count(label) for label in set(labels)))
            if n_folds >= 2:
                clf = CalibratedClassifierCV(base, method="sigmoid", cv=n_folds)

        model = Pipeline(
            [
                (
                    "features",
                    FeatureUnion(
                        [
                            (
                                "char",
                                TfidfVectorizer(
                                    analyzer="char_wb",
                                    ngram_range=(3, 5),
                                    min_df=1,
                                    sublinear_tf=True,
                                ),
                            ),
                            (
                                "word",
                                TfidfVectorizer(
                                    analyzer="word",
                                    ngram_range=(1, 2),
                                    min_df=1,
                                    sublinear_tf=True,
                                ),
                            ),
                        ]
                    ),
                ),
                ("clf", clf),
            ]
        )
        model.fit(texts, labels)
        return cls(model, version=version)

    def predict(self, text: str) -> ActionPrediction:
        normalized = normalize_for_action_model(text)
        probabilities = self._pipeline.predict_proba([normalized])[0]
        classes = list(self._pipeline.classes_)
        ranked = sorted(
            zip(classes, probabilities),
            key=lambda item: item[1],
            reverse=True,
        )
        top_label, top_prob = ranked[0]
        second_prob = ranked[1][1] if len(ranked) > 1 else 0.0
        action = SignalAction(top_label)
        return ActionPrediction(
            action=action,
            confidence=float(top_prob),
            margin=float(top_prob - second_prob),
        )

    def save(self, path: Path | None = None) -> Path:
        model_dir = path.parent if path is not None else DEFAULT_MODEL_DIR
        model_dir.mkdir(parents=True, exist_ok=True)
        out = path or (model_dir / f"action_clf-{self.version}.joblib")
        joblib.dump({"pipeline": self._pipeline, "version": self.version}, out)
        latest = model_dir / "action_clf-latest.joblib"
        joblib.dump({"pipeline": self._pipeline, "version": self.version}, latest)
        return out

    @classmethod
    def load(cls, path: Path | None = None) -> ActionClassifier | None:
        model_path = path or (DEFAULT_MODEL_DIR / "action_clf-latest.joblib")
        if not model_path.exists():
            return None
        payload = joblib.load(model_path)
        if isinstance(payload, dict):
            return cls(payload["pipeline"], version=str(payload.get("version", "loaded")))
        return cls(payload, version="loaded")


def examples_hash(examples: tuple[LabeledExample, ...]) -> str:
    digest = hashlib.sha256()
    for example in examples:
        digest.update(example.action.value.encode())
        digest.update(b"\0")
        digest.update(example.text.strip().lower().encode())
        digest.update(b"\n")
    return digest.hexdigest()[:12]
