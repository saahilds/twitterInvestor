from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.models.db_models import SignalAction

DEFAULT_LABELED_PATH = Path(__file__).resolve().parents[2] / "data" / "labeled_signals.jsonl"


@dataclass(frozen=True, slots=True)
class LabeledExample:
    text: str
    action: SignalAction
    source: str = "curated"


def load_labeled_examples(path: Path | None = None) -> tuple[LabeledExample, ...]:
    """Load labeled training examples from JSONL (curated + human reviews)."""
    labeled_path = path or DEFAULT_LABELED_PATH
    if not labeled_path.exists():
        raise FileNotFoundError(
            f"Labeled signals not found at {labeled_path}. "
            "Create data/labeled_signals.jsonl or run retrain_from_labels."
        )
    examples: list[LabeledExample] = []
    with labeled_path.open() as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {labeled_path}:{line_no}") from exc
            text = str(row.get("text") or "").strip()
            action_raw = str(row.get("action") or "").strip().upper()
            if not text or not action_raw:
                continue
            try:
                action = SignalAction(action_raw)
            except ValueError as exc:
                raise ValueError(
                    f"Unknown action {action_raw!r} at {labeled_path}:{line_no}"
                ) from exc
            examples.append(
                LabeledExample(
                    text=text,
                    action=action,
                    source=str(row.get("source") or "curated"),
                )
            )
    if not examples:
        raise ValueError(f"No labeled examples found in {labeled_path}")
    return tuple(examples)


def append_labeled_examples(
    examples: list[LabeledExample],
    path: Path | None = None,
) -> int:
    """Append examples to JSONL, skipping exact (text, action) duplicates. Returns added count."""
    labeled_path = path or DEFAULT_LABELED_PATH
    labeled_path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, str]] = set()
    if labeled_path.exists():
        for ex in load_labeled_examples(labeled_path):
            existing.add((ex.text.strip().lower(), ex.action.value))

    added = 0
    with labeled_path.open("a") as handle:
        for example in examples:
            key = (example.text.strip().lower(), example.action.value)
            if key in existing:
                continue
            handle.write(
                json.dumps(
                    {
                        "text": example.text,
                        "action": example.action.value,
                        "source": example.source,
                    }
                )
                + "\n"
            )
            existing.add(key)
            added += 1
    # Invalidate TRAINING_EXAMPLES proxy cache.
    _TrainingExamplesProxy._cache = None
    return added


# Lazy-loaded compatibility alias used by older imports / notebooks.
def _training_examples() -> tuple[LabeledExample, ...]:
    return load_labeled_examples()


class _TrainingExamplesProxy(tuple):
    """Tuple-like proxy that loads JSONL on first access."""

    _cache: tuple[LabeledExample, ...] | None = None

    def __new__(cls):
        return tuple.__new__(cls, ())

    def _ensure(self) -> tuple[LabeledExample, ...]:
        if _TrainingExamplesProxy._cache is None:
            _TrainingExamplesProxy._cache = load_labeled_examples()
        return _TrainingExamplesProxy._cache

    def __iter__(self):
        return iter(self._ensure())

    def __len__(self) -> int:
        return len(self._ensure())

    def __getitem__(self, index):
        return self._ensure()[index]


TRAINING_EXAMPLES = _TrainingExamplesProxy()
