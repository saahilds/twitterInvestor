from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

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
)

# Conditional / future tense — not an executed sell alert.
_SELL_HYPOTHETICAL = (
    r"\bwill look to sell\b",
    r"\bwill possibly look to sell\b",
    r"\blook to sell\b",
    r"\bwill sell\b",
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
    r"\bsold\b",
    r"\btrimmed\b",
    r"\btrimming\b",
    r"\btrim\b",
    r"\bclosed out\b",
    r"\bclosed\b",
    r"\bclose out\b",
    r"\bclose the\b",
    r"\bclose position\b",
    r"\btaking profit\b",
    r"\btake profit\b",
    r"\breduced\b",
    r"\breduce\b",
    r"\bended up trimming\b",
    r"\bfreeing up some cash\b",
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

    if _matches_any(_AFFIRMATIVE_SELL, snippet):
        return True

    if _matches_any(_PREEMPTIVE_SELL, snippet):
        return True

    if _matches_any(_SELL_HYPOTHETICAL, snippet):
        return False

    # Bare "sell" without past-tense confirmation is not enough.
    if re.search(r"\bsell\b", snippet) and not re.search(r"\bsold\b", snippet):
        return False

    return False
