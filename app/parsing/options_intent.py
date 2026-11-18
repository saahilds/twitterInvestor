"""Detect options-contract language the equity bot cannot execute yet."""

from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

# Equity execution must not treat these as underlying BUY/SELL.
_OPTIONS_CONTRACT = (
    r"\bputs?\b",
    r"\bcalls?\b",
    r"\boptions?\b",
    r"\$\d+(?:\.\d+)?\s+puts?\b",
    r"\$\d+(?:\.\d+)?\s+calls?\b",
    r"\bstrike\b",
    r"\bexpir(?:y|ing|es|ation)\b",
)


def is_options_contract_language(text: str) -> bool:
    """True when the trade is about puts/calls/options, not the underlying equity."""
    snippet = extract_action_snippet(text).lower()
    # Avoid "stay put" / "put together" false positives: require puts/calls plural
    # forms or explicit option markers when the bare singular appears alone.
    if re.search(r"\bstay put\b|\bput together\b|\bput up\b", snippet):
        cleaned = re.sub(r"\bstay put\b|\bput together\b|\bput up\b", " ", snippet)
        return any(re.search(pattern, cleaned) for pattern in _OPTIONS_CONTRACT)
    return any(re.search(pattern, snippet) for pattern in _OPTIONS_CONTRACT)
