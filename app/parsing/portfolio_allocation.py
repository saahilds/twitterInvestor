from __future__ import annotations

import re

from app.parsing.text_normalize import extract_action_snippet

# Portfolio allocation cues — avoid bare "%" (e.g. "30% off highs").
_PORTFOLIO_ALLOC = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)\s*(?:port(?:folio)?|position|weight)\b",
    re.IGNORECASE,
)
_STARTING_ALLOC = re.compile(
    r"starting\s+a\s+(\d+(?:\.\d+)?)\s*%\s*(?:\$)?",
    re.IGNORECASE,
)
_ADDITION_PORT = re.compile(
    r"(?:addition|added|adding|add)\s+(?:an?\s+)?(\d+(?:\.\d+)?)\s*%\s*port",
    re.IGNORECASE,
)
_ENTERED_WEIGHT = re.compile(
    r"(?:entered|entering|at)\s+a\s+(\d+(?:\.\d+)?)\s*%\s*weight",
    re.IGNORECASE,
)


def infer_portfolio_allocation_pct(text: str) -> float | None:
    """Extract an explicit portfolio allocation % from a buy tweet, if present."""
    snippet = extract_action_snippet(text)
    for pattern in (_ADDITION_PORT, _STARTING_ALLOC, _ENTERED_WEIGHT, _PORTFOLIO_ALLOC):
        match = pattern.search(snippet)
        if match is None:
            match = pattern.search(text)
        if match is not None:
            pct = float(match.group(1))
            if 0 < pct <= 100:
                return pct
    return None
