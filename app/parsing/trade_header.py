"""Detect CK-style Trade alert headers that force BUY/SELL classification."""

from __future__ import annotations

import re

# Lead-in like 🚨Trade🚨 / 🚨 TRADE 🚨 / Trade — optional alert emoji/separators.
_TRADE_HEADER = re.compile(
    r"^\s*(?:[\U0001F6A8\U0001F514⚠⚠️🚨]+\s*)*trade(?:\s*[\U0001F6A8\U0001F514⚠⚠️🚨]+)*\b",
    re.IGNORECASE,
)


def has_trade_header(text: str) -> bool:
    """True when the tweet opens with a Trade alert header."""
    if not text:
        return False
    # Strip leading BOM / zero-width and check the first non-empty line as well as the start.
    stripped = text.lstrip("\ufeff\u200b\u200c\u200d")
    if _TRADE_HEADER.search(stripped):
        return True
    for line in stripped.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        return bool(_TRADE_HEADER.match(candidate))
    return False
