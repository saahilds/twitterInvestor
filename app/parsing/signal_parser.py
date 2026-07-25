from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.db_models import SignalAction
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import infer_buy_conviction
from app.parsing.buy_intent import is_affirmative_buy_intent
from app.parsing.portfolio_allocation import infer_portfolio_allocation_pct
from app.parsing.sell_fraction import infer_sell_fraction
from app.parsing.sell_intent import is_affirmative_sell_intent
from app.parsing.signal_segments import SignalSegment, segment_trade_units
from app.parsing.watch_conviction import WatchConviction, infer_watch_conviction


class RuleBasedSignalParser:
    """Keyword and regex parser tuned for repetitive tweet signals."""

    def __init__(
        self,
        known_tickers: Iterable[str],
        default_sell_fraction: float = 1.0,
    ) -> None:
        self.known_tickers = {ticker.upper() for ticker in known_tickers}
        self.default_sell_fraction = default_sell_fraction
        self.cashtag_pattern = re.compile(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z])?)\b")
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
            "% port": 4,
            "port in": 3,
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
    ) -> list[TradeSignal]:
        """Parse a tweet into one or more trade signals (multi-ticker aware)."""
        raw_text = text.strip()
        known_tickers = self.known_tickers
        if extra_known_tickers:
            known_tickers = self.known_tickers | {ticker.upper() for ticker in extra_known_tickers}

        segments = segment_trade_units(raw_text)
        if not segments:
            ticker = self._extract_bare_ticker(raw_text, known_tickers)
            if ticker is None:
                return [self._ignore_signal(raw_text, source_tweet_id, reason_score=0)]
            segments = [
                SignalSegment(ticker=ticker, local_text=raw_text, start=0, end=len(raw_text))
            ]

        signals = [self._classify_segment(segment, source_tweet_id) for segment in segments]
        return self._dedupe_signals(signals)

    def _classify_segment(self, segment: SignalSegment, source_tweet_id: str) -> TradeSignal:
        local = segment.local_text
        normalized = local.lower()
        ticker = segment.ticker

        buy_score = self._score(normalized, self.buy_keywords)
        sell_score = self._score(normalized, self.sell_keywords)

        if buy_score == sell_score:
            watch = self._watch_signal(local, source_tweet_id, ticker, max(buy_score, sell_score))
            if watch is not None:
                return watch
            return self._ignore_signal(
                local, source_tweet_id, reason_score=max(buy_score, sell_score), ticker=ticker
            )

        action = SignalAction.BUY if buy_score > sell_score else SignalAction.SELL
        if action == SignalAction.SELL:
            watch = self._watch_signal(local, source_tweet_id, ticker, sell_score)
            if watch is not None:
                return watch
            if not is_affirmative_sell_intent(local):
                return self._ignore_signal(local, source_tweet_id, reason_score=sell_score, ticker=ticker)
        elif action == SignalAction.BUY and not is_affirmative_buy_intent(local):
            return self._ignore_signal(local, source_tweet_id, reason_score=buy_score, ticker=ticker)

        score = max(buy_score, sell_score)
# Align rule confidence with calibrated ML probabilities (~0.5–0.99).
        confidence = min(0.99, 0.50 + (score * 0.08))

        sell_fraction = None
        buy_conviction = None
        portfolio_allocation_pct = None
        if action == SignalAction.SELL:
            sell_fraction = infer_sell_fraction(local, default_fraction=self.default_sell_fraction)
        elif action == SignalAction.BUY:
            buy_conviction = infer_buy_conviction(local)
            portfolio_allocation_pct = infer_portfolio_allocation_pct(local)

        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=action,
            confidence=confidence,
            strength=self._strength_from_score(score),
            score=score,
            raw_text=local,
            suggested_trade_usd=0.0,
            sell_fraction=sell_fraction,
            buy_conviction=buy_conviction,
            portfolio_allocation_pct=portfolio_allocation_pct,
        )

    def _extract_bare_ticker(self, raw_text: str, known_tickers: set[str]) -> str | None:
        upper_text = raw_text.upper()
        for match in self.bare_ticker_pattern.finditer(upper_text):
            candidate = match.group(1).upper()
            if candidate in known_tickers:
                return candidate
        return None

    @staticmethod
    def _dedupe_signals(signals: list[TradeSignal]) -> list[TradeSignal]:
        """Prefer richer/non-ignore signal per ticker."""
        best: dict[str, TradeSignal] = {}
        order: list[str] = []
        for signal in signals:
            key = (signal.ticker or "").upper() or f"__{id(signal)}"
            existing = best.get(key)
            if existing is None:
                best[key] = signal
                order.append(key)
                continue
            if existing.action == SignalAction.IGNORE and signal.action != SignalAction.IGNORE:
                best[key] = signal
            elif (
                signal.action != SignalAction.IGNORE
                and signal.confidence >= existing.confidence
                and (
                    (signal.portfolio_allocation_pct is not None)
                    or (signal.sell_fraction is not None and existing.sell_fraction is None)
                )
            ):
                best[key] = signal
            elif signal.action != SignalAction.IGNORE and signal.confidence > existing.confidence:
                best[key] = signal
        return [best[key] for key in order]

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

    def _watch_signal(
        self,
        raw_text: str,
        source_tweet_id: str,
        ticker: str,
        reason_score: int,
    ) -> TradeSignal | None:
        watch_conviction = infer_watch_conviction(raw_text)
        if watch_conviction is None:
            return None
        confidence = self._watch_confidence(watch_conviction)
        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=SignalAction.WATCH,
            confidence=confidence,
            strength=watch_conviction.value,
            score=reason_score,
            raw_text=raw_text,
            suggested_trade_usd=0.0,
            watch_conviction=watch_conviction,
        )

    @staticmethod
    def _watch_confidence(conviction: WatchConviction) -> float:
        return {
            WatchConviction.SOFT: 0.45,
            WatchConviction.START: 0.55,
            WatchConviction.STANDARD: 0.65,
            WatchConviction.HEAVY: 0.80,
        }[conviction]

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
