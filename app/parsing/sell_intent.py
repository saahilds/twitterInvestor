from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet
from app.parsing.word_forms import word_family_regex

# Third-party / market commentary — not the account owner selling.
_SELL_COMMENTARY = (
    r"\bpeople sell\b",
    r"\bas people sell\b",
    r"\bsells off\b",
    r"\bsell[- ]off\b",
    r"\bsell the launch\b",
    r"\bsell the news\b",
    r"\bmarket sell\b",
    r"\bsell pressure\b",
    r"\bhaven'?t sold\b",
    r"\bhave not sold\b",
    r"\bhasn'?t sold\b",
    r"\bhas not sold\b",
    r"\bnot sold\b",
    # Corporate / thesis stats — not a live sell alert.
    r"\bthey sold\b",
    r"\bmore than they sold\b",
    r"\bsold all year\b",
    r"\bsold (?:in|during) (?:the |that )?year\b",
    r"\bbook to bill\b",
    r"\bbook[- ]to[- ]bill\b",
)

# Conditional / future tense — not an executed sell alert.
_SELL_HYPOTHETICAL = (
    r"\bwill look to sell\b",
    r"\bwill look to trim\b",
    r"\bwill possibly look to sell\b",
    r"\blook to sell\b",
    r"\blook to trim\b",
    r"\bwill sell\b",
    r"\bwill trim\b",
    r"\bmight sell\b",
    r"\bpossibly sell\b",
    r"\bplan to sell\b",
    r"\bconsider selling\b",
    r"\bif i sell\b",
)

# Imminent author sell intent (preemptive alerts).
_PREEMPTIVE_SELL = (
    r"\bgoing to sell before\b",
    r"\bgoing to sell\b",
)

# Past-tense or explicit trade-alert sell language.
_AFFIRMATIVE_SELL = (
    r"\bsold all\b",
    r"\bsold half\b",
    r"\bsold the rest\b",
    r"\bsold my\b",
    r"\bsold have\b",
    word_family_regex("sell"),
    word_family_regex("trim"),
    word_family_regex("cut"),
    r"\bclosed out\b",
    r"\bclose out\b",
    r"\bclose the\b",
    r"\bclose position\b",
    # Avoid bare "close" ("close to bottomed out" is not a sell).
    r"\bclos(?:ed|es|ing)\b",
    r"\btaking profit\b",
    r"\btake profit\b",
    word_family_regex("reduce"),
    r"\bended up trimming\b",
    r"\bfreeing up some cash\b",
    r"\bfreeing up cash\b",
    word_family_regex("downsize"),
)

# Recounting a past round-trip — not a live sell alert.
_PAST_TRADE_RECAP = (
    r"\beventually sold\b",
    r"\bcovered\b",
    r"\band sold at\b",
    r"\bhad sold\b",
    r"\bpreviously sold\b",
    r"\bsold at \$[\d.]+\.\s",
)


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def sell_suppressed_by_watch(text: str) -> bool:
    """Past trade recap + current watch interest — not a live sell alert."""
    from app.parsing.watch_conviction import infer_watch_conviction

    if infer_watch_conviction(text) is None:
        return False
    lower = text.lower()
    return _matches_any(_PAST_TRADE_RECAP, lower)


def is_affirmative_sell_intent(text: str) -> bool:
    """True when the tweet is an executed or explicit sell alert, not commentary."""
    snippet = extract_action_snippet(text).lower()

    if sell_suppressed_by_watch(text):
        return False
    if _matches_any(_SELL_COMMENTARY, snippet):
        return False

    # Remove future/conditional phrases so "will look to trim" does not
    # false-trigger on the bare "trim" token, while still allowing
    # "Trimming $ADEA … will look to trim elsewhere" to count as a sell.
    cleaned = snippet
    for pattern in _SELL_HYPOTHETICAL:
        cleaned = re.sub(pattern, " ", cleaned)

    if _matches_any(_AFFIRMATIVE_SELL, cleaned):
        return True
    if _matches_any(_PREEMPTIVE_SELL, cleaned):
        return True
    if _matches_any(_SELL_HYPOTHETICAL, snippet):
        return False
    return False
