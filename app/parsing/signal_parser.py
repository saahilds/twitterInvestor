from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.db_models import SignalAction
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import infer_buy_conviction
from app.parsing.sell_intent import is_affirmative_sell_intent
from app.parsing.sell_fraction import infer_sell_fraction
from app.parsing.text_normalize import extract_action_snippet


class RuleBasedSignalParser:
    """Keyword and regex parser tuned for repetitive tweet signals."""

    def __init__(
        self,
        known_tickers: Iterable[str],
        default_trade_size_usd: float = 1.0,
        default_sell_fraction: float = 1.0,
    ) -> None:
        self.known_tickers = {ticker.upper() for ticker in known_tickers}
        self.default_trade_size_usd = default_trade_size_usd
        self.default_sell_fraction = default_sell_fraction
        self.cashtag_pattern = re.compile(r"\$([A-Za-z]{1,5})\b")
        self.bare_ticker_pattern = re.compile(r"\b([A-Z]{1,5})\b")

        self.buy_keywords: dict[str, int] = {
            "took the position": 4,
            "took a position": 4,
            "took position": 4,
            "adding": 3,
            "starter": 3,
            "buy": 3,
            "bought": 3,
            "long": 2,
            "scale in": 2,
            "add": 2,
        }
        self.sell_keywords: dict[str, int] = {
            "trim": 3,
            "trimmed": 3,
            "sell": 3,
            "sold": 3,
            "closed": 4,
            "close": 4,
            "taking profit": 2,
            "reduce": 2,
        }

    def parse(
        self,
        text: str,
        source_tweet_id: str,
        *,
        extra_known_tickers: Iterable[str] | None = None,
    ) -> TradeSignal:
        """Parse a tweet into a basic trade signal."""
        raw_text = text.strip()
        upper_text = raw_text.upper()
        normalized = raw_text.lower()
        known_tickers = self.known_tickers
        if extra_known_tickers:
            known_tickers = self.known_tickers | {ticker.upper() for ticker in extra_known_tickers}

        ticker = self._extract_ticker(raw_text, upper_text, known_tickers)
        if ticker is None:
            return self._ignore_signal(raw_text, source_tweet_id, reason_score=0)

        buy_score = self._score(normalized, self.buy_keywords)
        sell_score = self._score(normalized, self.sell_keywords)

        if buy_score == sell_score:
            return self._ignore_signal(raw_text, source_tweet_id, reason_score=max(buy_score, sell_score), ticker=ticker)

        action = SignalAction.BUY if buy_score > sell_score else SignalAction.SELL
        if action == SignalAction.SELL and not is_affirmative_sell_intent(raw_text):
            return self._ignore_signal(raw_text, source_tweet_id, reason_score=sell_score, ticker=ticker)

        score = max(buy_score, sell_score)
        confidence = min(0.99, 0.45 + (score * 0.12))

        sell_fraction = None
        buy_conviction = None
        if action == SignalAction.SELL:
            sell_fraction = infer_sell_fraction(raw_text, default_fraction=self.default_sell_fraction)
        elif action == SignalAction.BUY:
            buy_conviction = infer_buy_conviction(raw_text)

        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=action,
            confidence=confidence,
            strength=self._strength_from_score(score),
            score=score,
            raw_text=raw_text,
            suggested_trade_usd=self.default_trade_size_usd,
            sell_fraction=sell_fraction,
            buy_conviction=buy_conviction,
        )

    def _extract_ticker(self, raw_text: str, upper_text: str, known_tickers: set[str]) -> str | None:
        """Pick the trade ticker from the action lead-in, not allowlisted symbols in thesis text."""
        action_snippet = extract_action_snippet(raw_text)
        action_upper = action_snippet.upper()

        cashtags = self._collect_cashtags(action_snippet)
        if not cashtags:
            cashtags = self._collect_cashtags(raw_text)
        if cashtags:
            return cashtags[0]

        for match in self.bare_ticker_pattern.finditer(action_upper):
            candidate = match.group(1).upper()
            if candidate in known_tickers:
                return candidate
        for match in self.bare_ticker_pattern.finditer(upper_text):
            candidate = match.group(1).upper()
            if candidate in known_tickers:
                return candidate
        return None

    def _collect_cashtags(self, text: str) -> list[str]:
        cashtags: list[str] = []
        for match in self.cashtag_pattern.finditer(text):
            candidate = match.group(1).upper()
            if candidate not in cashtags:
                cashtags.append(candidate)
        return cashtags

    @staticmethod
    def _score(text: str, keywords: dict[str, int]) -> int:
        score = 0
        for phrase, weight in sorted(keywords.items(), key=lambda item: len(item[0]), reverse=True):
            pattern = r"\b" + re.escape(phrase) + r"\b"
            if re.search(pattern, text):
                score += weight
        return score

    @staticmethod
    def _strength_from_score(score: int) -> str:
        if score >= 5:
            return "strong"
        if score >= 3:
            return "medium"
        if score >= 1:
            return "weak"
        return "none"

    def _ignore_signal(
        self,
        raw_text: str,
        source_tweet_id: str,
        reason_score: int,
        ticker: str | None = None,
    ) -> TradeSignal:
        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=SignalAction.IGNORE,
            confidence=0.0,
            strength="none",
            score=reason_score,
            raw_text=raw_text,
            suggested_trade_usd=0.0,
        )
