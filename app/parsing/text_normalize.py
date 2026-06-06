from __future__ import annotations

import re

_CASHTAG = re.compile(r"\$[A-Za-z]{1,5}\b")
_TICKER_TOKEN = "TICKER"
_WHITESPACE = re.compile(r"\s+")
_THESIS_SPLIT = re.compile(
    r"\bhere(?:'s| is) the (?:thesis|setup|trade|breakdown)\b",
    re.IGNORECASE,
)
_ACTION_SNIPPET_MAX_CHARS = 480


def extract_action_snippet(text: str, *, max_chars: int = _ACTION_SNIPPET_MAX_CHARS) -> str:
    """Prefer the opening lines where trade intent usually appears."""
    stripped = text.strip()
    parts = _THESIS_SPLIT.split(stripped, maxsplit=1)
    snippet = parts[0].strip() if parts else stripped
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars]
    return snippet or stripped


def normalize_for_action_model(text: str) -> str:
    """Lowercase and replace cashtags so the model focuses on intent phrasing."""
    snippet = extract_action_snippet(text)
    normalized = _CASHTAG.sub(_TICKER_TOKEN, snippet.lower())
    return _WHITESPACE.sub(" ", normalized).strip()
