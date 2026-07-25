from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

# Price-reaction / market commentary — not the author buying.
_BUY_COMMENTARY = (
    r"\bjust puked\b",
    r"\bpuked\b",
    r"\btanked\b",
    r"\bdumped\b",
    r"\bdumping\b",
    r"\bripped\b",
    r"\bripping\b",
    r"\bmelting\b",
    r"\bmelted\b",
    r"\bcrushed\b",
    r"\bsmashed\b",
    r"\bsold off\b",
    r"\bsells off\b",
    r"\bsell[- ]off\b",
    r"\bgot smoked\b",
    r"\bgetting smoked\b",
    r"\babsolutely flying\b",
    r"\bthis thing is absolutely flying\b",
    r"\bdown \d+(?:\.\d+)?%\b",
    r"\bup \d+(?:\.\d+)?%\b",
    r"\boff (?:its |the )?all[- ]?time highs\b",
    r"\bpeople buy\b",
    r"\bthey'?re buying\b",
    r"\bglad i alerted the subs to buy\b",
)

# Conditional / future / temptation — not an executed buy alert.
_BUY_HYPOTHETICAL = (
    r"\bmight (?:look to )?add\b",
    r"\bmight buy\b",
    r"\bconsider(?:ing)? (?:a |an )?buy\b",
    r"\btempting\b",
    r"\bwill not be adding\b",
    r"\bi will not be adding\b",
    r"\bnot in yet\b",
    r"\bno position yet\b",
    r"\bif i buy\b",
    r"\bplan to buy\b",
    r"\blook(?:ing)? to add\b",
)

# Explicit author buy / add language.
_AFFIRMATIVE_BUY = (
    r"\btook the position\b",
    r"\btook a position\b",
    r"\btook position\b",
    r"\bjust entered\b",
    r"\bi just entered\b",
    r"\bentered\b",
    r"\bentering\b",
    r"\bnew position\b",
    r"\bopened (?:a )?new position\b",
    r"\badding\b",
    r"\badded\b",
    r"\bbought\b",
    r"\bbuy\b",
    r"\bstarter\b",
    r"\bscaling in\b",
    r"\bscale in\b",
    r"\bgoing long\b",
    r"\b% port\b",
    r"\bport in\b",
    r"%\s*weight\b",
    r"\bgot back into\b",
    r"\bhad to take a position\b",
)


def _matches_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def is_affirmative_buy_intent(text: str) -> bool:
    """True when the tweet is an executed or explicit buy alert, not commentary."""
    snippet = extract_action_snippet(text).lower()

    if _matches_any(_BUY_COMMENTARY, snippet):
        # Commentary wins unless an explicit trade verb is also present
        # (e.g. "SPY puked so I added").
        cleaned = snippet
        for pattern in _BUY_COMMENTARY:
            cleaned = re.sub(pattern, " ", cleaned)
        return _matches_any(_AFFIRMATIVE_BUY, cleaned)

    cleaned = snippet
    for pattern in _BUY_HYPOTHETICAL:
        cleaned = re.sub(pattern, " ", cleaned)

    if _matches_any(_AFFIRMATIVE_BUY, cleaned):
        return True
    if _matches_any(_BUY_HYPOTHETICAL, snippet):
        return False
    return False
