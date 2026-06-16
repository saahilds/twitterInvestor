from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)\b", re.IGNORECASE)
_WORD_FRACTIONS: tuple[tuple[str, float], ...] = (
    ("three quarters", 0.75),
    ("two thirds", 2 / 3),
    ("one third", 1 / 3),
    ("a third", 1 / 3),
    ("three quarter", 0.75),
    ("two third", 2 / 3),
    ("one quarter", 0.25),
    ("a quarter", 0.25),
    ("half position", 0.5),
    ("half my", 0.5),
    ("half of", 0.5),
    ("half the", 0.5),
    ("sold half", 0.5),
    ("sell half", 0.5),
    ("half", 0.5),
    ("50%", 0.5),
)
_PHRASE_DEFAULTS: tuple[tuple[str, float], ...] = (
    ("trimmed", 0.25),
    ("trimming", 0.25),
    ("trim", 0.25),
    ("reduce", 0.5),
    ("reduced", 0.5),
    ("taking profit", 0.5),
    ("take profit", 0.5),
    ("closed out", 1.0),
    ("closed", 1.0),
    ("close out", 1.0),
    ("close", 1.0),
    ("sold all", 1.0),
    ("sell all", 1.0),
    ("out of", 1.0),
    ("sold", 1.0),
    ("sell", 1.0),
)


def infer_sell_fraction(text: str, *, default_fraction: float = 1.0) -> float:
    """Map sell tweet wording to a fraction of the open position (0–1)."""
    snippet = extract_action_snippet(text).lower()

    for match in _PERCENT.finditer(snippet):
        pct = float(match.group(1))
        if pct > 0:
            return min(1.0, pct / 100.0)

    for phrase, fraction in _WORD_FRACTIONS:
        if phrase in snippet:
            return fraction

    for phrase, fraction in sorted(_PHRASE_DEFAULTS, key=lambda item: len(item[0]), reverse=True):
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, snippet):
            return fraction

    return min(1.0, max(0.0, default_fraction))
