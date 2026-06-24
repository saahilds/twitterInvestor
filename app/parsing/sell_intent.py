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
)

# Conditional / future tense — not an executed sell alert.
_SELL_HYPOTHETICAL = (
    r"\bwill look to sell\b",
    r"\bwill possibly look to sell\b",
    r"\blook to sell\b",
    r"\bwill sell\b",
    r"\bmight sell\b",
    r"\bpossibly sell\b",
    r"\bgoing to sell\b",
    r"\bplan to sell\b",
    r"\bconsider selling\b",
    r"\bif i sell\b",
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


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def is_affirmative_sell_intent(text: str) -> bool:
    """True when the tweet is an executed or explicit sell alert, not commentary."""
    snippet = extract_action_snippet(text).lower()

    if _matches_any(_AFFIRMATIVE_SELL, snippet):
        return True

    if _matches_any(_SELL_COMMENTARY, snippet):
        return False

    if _matches_any(_SELL_HYPOTHETICAL, snippet):
        return False

    # Bare "sell" without past-tense confirmation is not enough.
    if re.search(r"\bsell\b", snippet) and not re.search(r"\bsold\b", snippet):
        return False

    return False
