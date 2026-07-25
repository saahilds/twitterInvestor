from __future__ import annotations

from app.models.db_models import SignalAction
from app.parsing.evaluation import evaluate_holdout, suggested_thresholds
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.ml_action_classifier import ActionClassifier
from app.parsing.training_examples import load_labeled_examples

MULTI_TWEET = """New positions

Hey guys,

Adding 2.1% port in 
$INTC


Also adding 3.8% port in 
$META


Trimming 
$ADEA
 from 5% to 4%

Digging into margin here so will look to trim somewhere else today.

These are some large cap stocks I like that will outweigh some of the high growth we have."""


def test_labeled_dataset_loads() -> None:
    examples = load_labeled_examples()
    assert len(examples) >= 50
    actions = {ex.action for ex in examples}
    assert SignalAction.BUY in actions
    assert SignalAction.SELL in actions
    assert SignalAction.IGNORE in actions
    assert SignalAction.WATCH in actions


def test_classifier_holdout_macro_f1_baseline() -> None:
    examples = load_labeled_examples()
    report = evaluate_holdout(examples, test_size=0.25, random_state=7)
    # Guardrail: tiny curated set should still generalize modestly.
    assert report.macro_f1 >= 0.35
    assert report.support >= 5


def test_classifier_predicts_trim_as_sell() -> None:
    clf = ActionClassifier.train(calibrate=False)
    pred = clf.predict("trimmed $META today")
    assert pred.action == SignalAction.SELL
    assert pred.confidence > 0.3


def test_suggested_thresholds_are_sane() -> None:
    conf, margin = suggested_thresholds()
    assert 0.25 <= conf <= 0.7
    assert 0.02 <= margin <= 0.2


def test_multi_signal_recall_golden_case() -> None:
    parser = HybridSignalParser(known_tickers=["INTC", "META", "ADEA"])
    signals = parser.parse(MULTI_TWEET, source_tweet_id="metrics-multi")
    actionable = {s.ticker: s for s in signals if s.action != SignalAction.IGNORE}
    assert set(actionable) == {"INTC", "META", "ADEA"}
    assert actionable["INTC"].action == SignalAction.BUY
    assert actionable["META"].action == SignalAction.BUY
    assert actionable["ADEA"].action == SignalAction.SELL


def test_model_save_and_load(tmp_path) -> None:
    clf = ActionClassifier.train(calibrate=False)
    path = clf.save(tmp_path / "action_clf-test.joblib")
    loaded = ActionClassifier.load(path)
    assert loaded is not None
    assert loaded.predict("buy $TSLA here").action == SignalAction.BUY
