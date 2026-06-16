from __future__ import annotations

from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from app.models.db_models import SignalAction
from app.parsing.text_normalize import normalize_for_action_model
from app.parsing.training_examples import TRAINING_EXAMPLES, LabeledExample


@dataclass(frozen=True, slots=True)
class ActionPrediction:
    action: SignalAction
    confidence: float
    margin: float


class ActionClassifier:
    """Lightweight buy/sell/ignore classifier (TF-IDF + logistic regression)."""

    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    @classmethod
    def train(cls, examples: tuple[LabeledExample, ...] = TRAINING_EXAMPLES) -> ActionClassifier:
        texts = [normalize_for_action_model(example.text) for example in examples]
        labels = [example.action.value for example in examples]
        model = Pipeline(
            [
                (
                    "tfidf",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=(3, 5),
                        min_df=1,
                        sublinear_tf=True,
                    ),
                ),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=2_000,
                        class_weight="balanced",
                    ),
                ),
            ]
        )
        model.fit(texts, labels)
        return cls(model)

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
