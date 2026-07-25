from __future__ import annotations

import re
from dataclasses import dataclass

# Cashtags including class shares like $BRK.B
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b")

# Start of a new trade action — used to split multi-ticker tweets.
_ACTION_BOUNDARY = re.compile(
    r"(?i)(?:^|\n)\s*(?:"
    r"also\s+)?"
    r"(?:"
    r"adding|added|add\b|buying|bought|buy\b|"
    r"trimming|trimmed|trim\b|"
    r"selling|sold|sell\b|"
    r"closing|closed|close\b|"
    r"entered|entering|opened|opening|"
    r"taking profit|took (?:a |the )?position|starter|"
    r"scaling|scale in|reducing|reduced|reduce\b"
    r")"
)


@dataclass(frozen=True, slots=True)
class SignalSegment:
    """One ticker-scoped slice of a tweet ready to classify."""

    ticker: str
    local_text: str
    start: int
    end: int


def segment_trade_units(text: str) -> list[SignalSegment]:
    """Split a tweet into per-ticker action snippets.

    Multi-cashtag posts (e.g. adding INTC + META, trimming ADEA) become one
    segment per cashtag. Single-cashtag tweets return one segment over the
    full actionable text.
    """
    stripped = text.strip()
    if not stripped:
        return []

    matches = list(_CASHTAG.finditer(stripped))
    if not matches:
        return []

    if len(matches) == 1:
        match = matches[0]
        return [
            SignalSegment(
                ticker=match.group(1).upper(),
                local_text=stripped,
                start=0,
                end=len(stripped),
            )
        ]

    # Prefer action-phrase boundaries; fall back to midpoints between cashtags.
    boundaries = _action_split_points(stripped)
    segments: list[SignalSegment] = []
    for index, match in enumerate(matches):
        ticker = match.group(1).upper()
        start = _segment_start(stripped, match.start(), index, matches, boundaries)
        end = _segment_end(stripped, match.end(), index, matches, boundaries)
        local = stripped[start:end].strip()
        if not local:
            # Minimal fallback: a window around the cashtag.
            local = stripped[max(0, match.start() - 80) : min(len(stripped), match.end() + 80)].strip()
        segments.append(SignalSegment(ticker=ticker, local_text=local, start=start, end=end))

    return _dedupe_segments(segments)


def _action_split_points(text: str) -> list[int]:
    return [match.start() for match in _ACTION_BOUNDARY.finditer(text)]


def _segment_start(
    text: str,
    cashtag_start: int,
    index: int,
    matches: list[re.Match[str]],
    boundaries: list[int],
) -> int:
    prior_end = matches[index - 1].end() if index > 0 else 0
    # Nearest action boundary at or before this cashtag, after the prior cashtag.
    candidates = [b for b in boundaries if prior_end <= b <= cashtag_start]
    if candidates:
        return candidates[-1]
    if index == 0:
        return 0
    # Midpoint between prior cashtag and this one.
    return (prior_end + cashtag_start) // 2


def _segment_end(
    text: str,
    cashtag_end: int,
    index: int,
    matches: list[re.Match[str]],
    boundaries: list[int],
) -> int:
    if index + 1 >= len(matches):
        return len(text)
    next_start = matches[index + 1].start()
    # Nearest action boundary for the next segment.
    candidates = [b for b in boundaries if cashtag_end <= b <= next_start]
    if candidates:
        return candidates[0]
    return (cashtag_end + next_start) // 2


def _dedupe_segments(segments: list[SignalSegment]) -> list[SignalSegment]:
    """Keep first occurrence of each ticker (CK often repeats thesis cashtags)."""
    seen: set[str] = set()
    unique: list[SignalSegment] = []
    for segment in segments:
        if segment.ticker in seen:
            continue
        # Skip thesis-only repeats that share almost no action language.
        seen.add(segment.ticker)
        unique.append(segment)
    return unique
