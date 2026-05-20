from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.db_models import SignalAction
from app.models.schemas import TradeSignal


class RuleBasedSignalParser:
    """Keyword and regex parser tuned for repetitive tweet signals."""

    def __init__(self, known_tickers: Iterable[str], default_trade_size_usd: float = 1.0) -> None:
        self.known_tickers = {ticker.upper() for ticker in known_tickers}
        self.default_trade_size_usd = default_trade_size_usd
        self.ticker_pattern = re.compile(r"(?:\$)?([A-Z]{1,5})\b")

        self.buy_keywords: dict[str, int] = {
            "adding": 3,
            "add": 2,
            "starter": 3,
            "buy": 3,
            "bought": 3,
            "long": 2,
            "scale in": 2,
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

    def parse(self, text: str, source_tweet_id: str) -> TradeSignal:
        """Parse a tweet into a basic trade signal."""
        raw_text = text.strip()
        upper_text = raw_text.upper()
        normalized = raw_text.lower()

        ticker = self._extract_ticker(upper_text)
        if ticker is None:
            return self._ignore_signal(raw_text, source_tweet_id, reason_score=0)

        buy_score = self._score(normalized, self.buy_keywords)
        sell_score = self._score(normalized, self.sell_keywords)

        if buy_score == sell_score:
            return self._ignore_signal(raw_text, source_tweet_id, reason_score=max(buy_score, sell_score), ticker=ticker)

        action = SignalAction.BUY if buy_score > sell_score else SignalAction.SELL
        score = max(buy_score, sell_score)
        confidence = min(0.99, 0.45 + (score * 0.12))

        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=action,
            confidence=confidence,
            strength=self._strength_from_score(score),
            score=score,
            raw_text=raw_text,
            suggested_trade_usd=self.default_trade_size_usd,
        )

    def _extract_ticker(self, upper_text: str) -> str | None:
        seen: list[str] = []
        for match in self.ticker_pattern.finditer(upper_text):
            candidate = match.group(1).upper()
            if candidate in self.known_tickers and candidate not in seen:
                seen.append(candidate)
        return seen[0] if seen else None

    @staticmethod
    def _score(text: str, keywords: dict[str, int]) -> int:
        score = 0
        for phrase, weight in keywords.items():
            if phrase in text:
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
