from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

_FROM_TO_WEIGHT = re.compile(
    r"from\s+(\d+(?:\.\d+)?)\s*(?:%|percent|pct)\s+to\s+(\d+(?:\.\d+)?)\s*(?:%|percent|pct)",
    re.IGNORECASE,
)
_DOWN_TO_WEIGHT = re.compile(
    r"(?:down\s+to|trim(?:med|ming)?\s+(?:down\s+)?to|reduce(?:d|s)?\s+to)"
    r"\s+(\d+(?:\.\d+)?)\s*(?:%|percent|pct)",
    re.IGNORECASE,
)
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


def infer_target_portfolio_pct(text: str) -> float | None:
    """Extract a target portfolio weight % from 'down to / trimmed to X%' phrasing.

    Does not match ``from A% to B%`` (that remains a relative sell fraction).
    """
    snippet = extract_action_snippet(text)
    # Prefer from-to as relative sizing — not a target weight.
    if _FROM_TO_WEIGHT.search(snippet) or _FROM_TO_WEIGHT.search(text):
        return None
    match = _DOWN_TO_WEIGHT.search(snippet) or _DOWN_TO_WEIGHT.search(text)
    if match is None:
        return None
    pct = float(match.group(1))
    if 0 < pct <= 100:
        return pct
    return None


def has_explicit_sell_sizing(text: str) -> bool:
    """Return whether the tweet gives an exact sell fraction/target/full exit."""
    snippet = extract_action_snippet(text).lower()
    full = text.lower()
    if infer_target_portfolio_pct(text) is not None:
        return True
    if _FROM_TO_WEIGHT.search(snippet) or _FROM_TO_WEIGHT.search(full):
        return True
    if _PERCENT.search(snippet):
        return True
    if any(phrase in snippet for phrase, _ in _WORD_FRACTIONS):
        return True
    full_exit_phrases = (
        "closed out",
        "closed",
        "close out",
        "close",
        "sold all",
        "sell all",
        "out of",
        "sold",
        "sell",
    )
    return any(
        re.search(r"\b" + re.escape(phrase) + r"\b", snippet)
        for phrase in full_exit_phrases
    )


def infer_sell_fraction(text: str, *, default_fraction: float = 1.0) -> float:
    """Map sell tweet wording to a fraction of the open position (0–1)."""
    snippet = extract_action_snippet(text).lower()
    full = text.lower()

    from_to = _FROM_TO_WEIGHT.search(snippet) or _FROM_TO_WEIGHT.search(full)
    if from_to is not None:
        start_pct = float(from_to.group(1))
        end_pct = float(from_to.group(2))
        if start_pct > end_pct > 0:
            return min(1.0, max(0.0, (start_pct - end_pct) / start_pct))

    # Target-weight trims are sized in risk; fall through to phrase defaults (e.g. trim→0.25).
    if infer_target_portfolio_pct(text) is not None:
        for phrase, fraction in sorted(_PHRASE_DEFAULTS, key=lambda item: len(item[0]), reverse=True):
            pattern = r"\b" + re.escape(phrase) + r"\b"
            if re.search(pattern, snippet) or re.search(pattern, full):
                return fraction
        return min(1.0, max(0.0, default_fraction))

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
