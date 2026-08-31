from __future__ import annotations

import re

from sqlalchemy import ColumnElement, func, or_

# Cashtags including class shares like $BRK.B (same shape as signal_segments).
CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b", re.IGNORECASE)

# Bare tickers: 1–5 letters, optional class share; not preceded by a letter or $.
BARE_TICKER_RE = re.compile(r"(?<![A-Za-z$])([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b")


def text_mentions_ticker(text: str | None, prefix: str) -> bool:
    """Return True if tweet text mentions a ticker starting with ``prefix``."""
    if not text or not prefix:
        return False
    needle = prefix.strip().upper()
    if not needle:
        return False

    for match in CASHTAG_RE.finditer(text):
        if match.group(1).upper().startswith(needle):
            return True
    for match in BARE_TICKER_RE.finditer(text):
        if match.group(1).upper().startswith(needle):
            return True
    return False


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ticker_text_match_clause(text_column: ColumnElement[str], prefix: str) -> ColumnElement[bool]:
    """SQLAlchemy OR clause: tweet text contains cashtag or bare ticker for prefix."""
    safe = _escape_like(prefix.upper())
    upper = func.upper(text_column)
    # Cashtag anywhere (covers "$AAOI" even across newlines).
    clauses: list[ColumnElement[bool]] = [upper.like(f"%${safe}%", escape="\\")]
    # Bare ticker with common separators / string edges (SQLite has no lookbehind).
    bare_patterns = (
        safe,
        f"{safe} %",
        f"% {safe}",
        f"% {safe} %",
        f"{safe}\n%",
        f"%\n{safe}",
        f"%\n{safe}\n%",
        f"% {safe}\n%",
        f"%\n{safe} %",
        f"% {safe},%",
        f"% {safe}.%",
        f"{safe},%",
        f"%,{safe} %",
        f"%,{safe},%",
    )
    clauses.extend(upper.like(pattern, escape="\\") for pattern in bare_patterns)
    return or_(*clauses)
