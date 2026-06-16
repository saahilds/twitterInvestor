from __future__ import annotations

import re
from enum import Enum

from app.parsing.text_normalize import extract_action_snippet, has_thesis_marker, thesis_body_length

_PERCENT_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)\s*weight\b", re.IGNORECASE)

_CONVICTION_PHRASES: tuple[str, ...] = (
    "took the position",
    "took a position",
    "took position",
    "just entered",
    "new position for the subs",
    "i just entered",
    "entered",
)

_RELOAD_PHRASES: tuple[str, ...] = (
    "bought more",
    "scaling in",
    "scale in",
    "reloading",
    "reload",
    "adding",
    "starter",
)


class BuyConviction(str, Enum):
    RELOAD = "reload"
    STANDARD = "standard"
    THESIS = "thesis"


def infer_buy_conviction(text: str) -> BuyConviction:
    """Classify buy tweet conviction for trade sizing."""
    snippet = extract_action_snippet(text).lower()
    full_lower = text.lower()

    if _is_thesis_conviction(snippet, full_lower, text):
        return BuyConviction.THESIS

    for phrase in _RELOAD_PHRASES:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, snippet):
            return BuyConviction.RELOAD

    return BuyConviction.STANDARD


def _is_thesis_conviction(snippet: str, full_lower: str, raw_text: str) -> bool:
    if has_thesis_marker(raw_text):
        return True

    if _PERCENT_WEIGHT.search(snippet):
        return True

    has_conviction_phrase = any(phrase in snippet for phrase in _CONVICTION_PHRASES)
    if has_conviction_phrase and thesis_body_length(raw_text) >= 300:
        return True

    if has_conviction_phrase and "new position" in snippet:
        return True

    return False
