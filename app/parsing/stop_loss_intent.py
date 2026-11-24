"""Detect stop-loss *setup* language (not yet a supported order type)."""

from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

# Live bot cannot place stop orders yet — treat these as IGNORE until STOP_LOSS
# is a first-class action. Prefer setup phrasing over "stopped out" fills.
_STOP_LOSS_SETUP = (
    r"\bstop[- ]?loss(?:es)?\b",
    r"\bset(?:ting)? (?:a |my |the )?stop\b",
    r"\bstops? set (?:at|@)\b",
    r"\bstop (?:set |placed )?(?:at|@)\b",
    r"\bplaced (?:a |my |the )?stop\b",
)


def is_stop_loss_setup(text: str) -> bool:
    """True when the author is setting / announcing a stop loss, not selling now."""
    snippet = extract_action_snippet(text).lower()
    return any(re.search(pattern, snippet) for pattern in _STOP_LOSS_SETUP)
