"""Expand verb stems into common English surface forms for signal matching."""

from __future__ import annotations

import re

# Irregular buy/sell conjugations that suffix rules miss.
_IRREGULAR_FORMS: dict[str, frozenset[str]] = {
    "buy": frozenset({"buy", "buys", "buying", "bought"}),
    "sell": frozenset({"sell", "sells", "selling", "sold"}),
    # Bare "close" is too common in English ("close to"); require closed/closing/closes.
    "close": frozenset({"closed", "closes", "closing"}),
}

# Single-token keywords that should stay exact (not verb-inflected).
_EXACT_TOKENS = frozenset({"long", "starter"})

_VOWELS = set("aeiou")


def _doubles_final_consonant(stem: str) -> bool:
    """CVC short-verb heuristic: trim→trimmed, add→added."""
    if len(stem) < 3:
        return False
    a, b, c = stem[-3], stem[-2], stem[-1]
    return a not in _VOWELS and b in _VOWELS and c not in _VOWELS and c not in "wxy"


def inflection_forms(stem: str) -> frozenset[str]:
    """Return stem plus regular (or irregular) verb conjugations."""
    stem = stem.lower().strip()
    if not stem:
        return frozenset()
    if stem in _IRREGULAR_FORMS:
        return _IRREGULAR_FORMS[stem]
    if " " in stem or "%" in stem or stem in _EXACT_TOKENS:
        return frozenset({stem})

    forms = {stem}
    if stem.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(stem + "es")
    elif stem.endswith("y") and len(stem) > 1 and stem[-2] not in _VOWELS:
        forms.add(stem[:-1] + "ies")
    else:
        forms.add(stem + "s")

    if stem.endswith("e"):
        forms.add(stem + "d")
        forms.add(stem[:-1] + "ing")
    elif _doubles_final_consonant(stem):
        forms.add(stem + stem[-1] + "ed")
        forms.add(stem + stem[-1] + "ing")
    else:
        forms.add(stem + "ed")
        forms.add(stem + "ing")
    return frozenset(forms)


def keyword_match_pattern(phrase: str) -> re.Pattern[str]:
    """Compile a word-boundary pattern that matches a keyword and its conjugations."""
    phrase = phrase.lower().strip()
    forms = sorted(inflection_forms(phrase), key=len, reverse=True)
    if len(forms) == 1:
        return re.compile(r"\b" + re.escape(forms[0]) + r"\b", re.IGNORECASE)
    alternation = "|".join(re.escape(form) for form in forms)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def word_family_regex(stem: str) -> str:
    """Regex string for a stem and its conjugations (text should already be lowercased)."""
    return keyword_match_pattern(stem).pattern
