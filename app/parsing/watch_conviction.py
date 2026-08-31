from __future__ import annotations

import re
from enum import Enum

from app.parsing.text_normalize import extract_action_snippet

_HEAVY_PHRASES: tuple[str, ...] = (
    "heavy watch",
    "one of my favorite longs",
    "favorite longs right now",
    "getting pretty cheap",
    "stock trading at",
    "very interesting to enter",
    "bottom in might be in",
)

_START_PHRASES: tuple[str, ...] = (
    "start to watch",
    "starting to watch",
    "start watching",
    "starting to look",
    "look attractive",
)

_SOFT_PHRASES: tuple[str, ...] = (
    "soft watchlist",
    "soft watch",
    "would deploy into if",
    "if i did deploy some cash",
    "might look to add",
    "possibly start a position",
    "not in yet but might",
)

_STANDARD_PHRASES: tuple[str, ...] = (
    "watching",
    "watch ",
    "keep an eye",
    "look interesting",
    "worth watching",
    "on my radar",
    "keeping an eye",
    "lots of green",
    "massive gainers",
    "publicly vouched for",
    "vouched for",
)


class WatchConviction(str, Enum):
    SOFT = "soft_watch"
    START = "start_watch"
    STANDARD = "watch"
    HEAVY = "heavy_watch"


_CONVICTION_SCORE_DELTA: dict[WatchConviction, float] = {
    WatchConviction.SOFT: 0.5,
    WatchConviction.START: 0.75,
    WatchConviction.STANDARD: 1.0,
    WatchConviction.HEAVY: 2.0,
}

_SIZE_MULTIPLIER: dict[WatchConviction, float] = {
    WatchConviction.SOFT: 1.05,
    WatchConviction.START: 1.10,
    WatchConviction.STANDARD: 1.15,
    WatchConviction.HEAVY: 1.30,
}


def conviction_score_delta(conviction: WatchConviction) -> float:
    return _CONVICTION_SCORE_DELTA[conviction]


def watch_size_multiplier(conviction: WatchConviction, conviction_score: float) -> float:
    """Tier boost plus accumulated watch history (+2% per score point, max +10%)."""
    tier = _SIZE_MULTIPLIER[conviction]
    history = 1.0 + min(max(conviction_score, 0.0) * 0.02, 0.10)
    return tier * history


def infer_watch_conviction(text: str) -> WatchConviction | None:
    """Return watch tier when tweet expresses speculative interest without a trade alert."""
    snippet = extract_action_snippet(text).lower()
    full_lower = text.lower()

    for phrase in _START_PHRASES:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, snippet) or re.search(pattern, full_lower):
            return WatchConviction.START

    for phrase in _HEAVY_PHRASES:
        if phrase in snippet or phrase in full_lower:
            return WatchConviction.HEAVY

    for phrase in _SOFT_PHRASES:
        if phrase in snippet or phrase in full_lower:
            return WatchConviction.SOFT

    for phrase in _STANDARD_PHRASES:
        pattern = r"\b" + re.escape(phrase.rstrip()) + r"\b"
        if re.search(pattern, snippet) or re.search(pattern, full_lower):
            return WatchConviction.STANDARD

    return None
