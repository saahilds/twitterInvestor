"""Detect options-contract language the equity bot cannot execute yet."""

from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

# Equity execution must not treat these as underlying BUY/SELL.
# Do NOT match bare singular "put"/"call" — that catches verbs like
# "Put 2% into $COHR" and drops real equity buys.
_OPTIONS_CONTRACT = (
    r"\bputs\b",
    r"\bcalls\b",
    r"\bput options?\b",
    r"\bcall options?\b",
    r"\boptions\b",
    r"\$\d+(?:\.\d+)?\s+puts?\b",
    r"\$\d+(?:\.\d+)?\s+calls?\b",
    r"\bstrike\b",
    r"\bexpir(?:y|ing|es|ation)\b",
)


def is_options_contract_language(text: str) -> bool:
    """True when the trade is about puts/calls/options, not the underlying equity."""
    snippet = extract_action_snippet(text).lower()
    return any(re.search(pattern, snippet) for pattern in _OPTIONS_CONTRACT)
