from __future__ import annotations

from collections.abc import Iterable

from app.models.db_models import SignalAction
from app.models.schemas import TradeSignal
from app.parsing.buy_conviction import infer_buy_conviction
from app.parsing.buy_intent import is_affirmative_buy_intent
from app.parsing.ml_action_classifier import ActionClassifier, ActionPrediction
from app.parsing.portfolio_allocation import infer_portfolio_allocation_pct
from app.parsing.sell_fraction import (
    has_explicit_sell_sizing,
    infer_sell_fraction,
    infer_target_portfolio_pct,
)
from app.parsing.sell_intent import is_affirmative_sell_intent
from app.parsing.signal_parser import RuleBasedSignalParser
from app.parsing.signal_segments import SignalSegment, segment_trade_units
from app.parsing.trade_header import has_trade_header


class HybridSignalParser:
    """Rules for ticker extraction; keywords + small ML model for buy/sell/ignore."""

    def __init__(
        self,
        known_tickers: Iterable[str],
        default_sell_fraction: float = 1.0,
        *,
        action_classifier: ActionClassifier | None = None,
        ml_min_confidence: float = 0.42,
        ml_min_margin: float = 0.08,
        keyword_clear_score: int = 3,
        trade_header_review_confidence: float = 0.5,
    ) -> None:
        self._rules = RuleBasedSignalParser(
            known_tickers=known_tickers,
            default_sell_fraction=default_sell_fraction,
        )
        self._classifier = action_classifier or ActionClassifier.train()
        self._ml_min_confidence = ml_min_confidence
        self._ml_min_margin = ml_min_margin
        self._keyword_clear_score = keyword_clear_score
        self._trade_header_review_confidence = trade_header_review_confidence

    def parse(
        self,
        text: str,
        source_tweet_id: str,
        *,
        extra_known_tickers: Iterable[str] | None = None,
    ) -> list[TradeSignal]:
        raw_text = text.strip()
        force_trade = has_trade_header(raw_text)
        segments = segment_trade_units(raw_text)
        if not segments:
            # Fall back to rule parser (bare ticker / no cashtag cases).
            return self._rules.parse(
                raw_text,
                source_tweet_id,
                extra_known_tickers=extra_known_tickers,
            )

        shared_action = self._infer_shared_tweet_action(raw_text, segments)
        signals = [
            self._classify_segment(
                segment,
                source_tweet_id,
                force_trade=force_trade,
                shared_action=shared_action,
                tweet_text=raw_text if shared_action is not None else None,
            )
            for segment in segments
        ]
        return RuleBasedSignalParser._dedupe_signals(signals)

    def _infer_shared_tweet_action(
        self,
        raw_text: str,
        segments: list[SignalSegment],
    ) -> SignalAction | None:
        """When one buy/sell phrase governs multiple tickers, reuse it for every segment."""
        if len(segments) <= 1:
            return None

        normalized = raw_text.lower()
        buy_score = RuleBasedSignalParser._score(normalized, self._rules.buy_keywords)
        sell_score = RuleBasedSignalParser._score(normalized, self._rules.sell_keywords)

        segment_keyword_actions: list[SignalAction] = []
        for segment in segments:
            local = segment.local_text.lower()
            local_buy = RuleBasedSignalParser._score(local, self._rules.buy_keywords)
            local_sell = RuleBasedSignalParser._score(local, self._rules.sell_keywords)
            if local_buy > local_sell and local_buy > 0:
                segment_keyword_actions.append(SignalAction.BUY)
            elif local_sell > local_buy and local_sell > 0:
                segment_keyword_actions.append(SignalAction.SELL)

        if len(set(segment_keyword_actions)) > 1:
            return None

        if (
            sell_score > buy_score
            and sell_score > 0
            and is_affirmative_sell_intent(raw_text)
            and SignalAction.BUY not in segment_keyword_actions
        ):
            return SignalAction.SELL
        if (
            buy_score > sell_score
            and buy_score > 0
            and is_affirmative_buy_intent(raw_text)
            and SignalAction.SELL not in segment_keyword_actions
        ):
            return SignalAction.BUY

        affirmative_sell = is_affirmative_sell_intent(raw_text)
        affirmative_buy = is_affirmative_buy_intent(raw_text)
        if affirmative_sell and not affirmative_buy and SignalAction.BUY not in segment_keyword_actions:
            return SignalAction.SELL
        if affirmative_buy and not affirmative_sell and SignalAction.SELL not in segment_keyword_actions:
            return SignalAction.BUY

        return None

    def _classify_segment(
        self,
        segment: SignalSegment,
        source_tweet_id: str,
        *,
        force_trade: bool = False,
        shared_action: SignalAction | None = None,
        tweet_text: str | None = None,
    ) -> TradeSignal:
        if shared_action is not None:
            context = (tweet_text or segment.local_text).lower()
            keywords = (
                self._rules.sell_keywords
                if shared_action == SignalAction.SELL
                else self._rules.buy_keywords
            )
            score = max(RuleBasedSignalParser._score(context, keywords), 3)
            return self._build_sized_signal(
                raw_text=tweet_text or segment.local_text,
                source_tweet_id=source_tweet_id,
                ticker=segment.ticker,
                action=shared_action,
                score=score,
            )

        if force_trade:
            return self._force_trade_segment(segment, source_tweet_id)

        local = segment.local_text
        rule_signal = self._rules._classify_segment(segment, source_tweet_id)

        if rule_signal.action == SignalAction.WATCH:
            return rule_signal

        keyword_action = (
            rule_signal.action
            if rule_signal.action != SignalAction.IGNORE
            and rule_signal.score >= self._keyword_clear_score
            else None
        )
        if keyword_action is not None:
            if keyword_action == SignalAction.SELL and not is_affirmative_sell_intent(local):
                return self._rules._ignore_signal(
                    local,
                    source_tweet_id,
                    reason_score=rule_signal.score,
                    ticker=rule_signal.ticker,
                )
            if keyword_action == SignalAction.BUY and not is_affirmative_buy_intent(local):
                return self._rules._ignore_signal(
                    local,
                    source_tweet_id,
                    reason_score=rule_signal.score,
                    ticker=rule_signal.ticker,
                )
            return rule_signal

        ml_prediction = self._classifier.predict(local)
        if self._ml_usable(ml_prediction):
            if ml_prediction.action == SignalAction.SELL and not is_affirmative_sell_intent(local):
                return self._rules._ignore_signal(
                    local,
                    source_tweet_id,
                    reason_score=rule_signal.score,
                    ticker=rule_signal.ticker,
                )
            if ml_prediction.action == SignalAction.BUY and not is_affirmative_buy_intent(local):
                return self._rules._ignore_signal(
                    local,
                    source_tweet_id,
                    reason_score=rule_signal.score,
                    ticker=rule_signal.ticker,
                )
            return self._from_ml(
                raw_text=local,
                source_tweet_id=source_tweet_id,
                ticker=segment.ticker,
                prediction=ml_prediction,
            )

        if rule_signal.action != SignalAction.IGNORE:
            if rule_signal.action == SignalAction.SELL and not is_affirmative_sell_intent(local):
                watch = self._rules._watch_signal(
                    local,
                    source_tweet_id,
                    segment.ticker,
                    rule_signal.score,
                )
                if watch is not None:
                    return watch
                return self._rules._ignore_signal(
                    local,
                    source_tweet_id,
                    reason_score=rule_signal.score,
                    ticker=segment.ticker,
                )
            if rule_signal.action == SignalAction.BUY and not is_affirmative_buy_intent(local):
                return self._rules._ignore_signal(
                    local,
                    source_tweet_id,
                    reason_score=rule_signal.score,
                    ticker=segment.ticker,
                )
            return rule_signal

        watch = self._rules._watch_signal(
            local,
            source_tweet_id,
            segment.ticker,
            rule_signal.score,
        )
        if watch is not None:
            return watch

        return rule_signal

    def _force_trade_segment(
        self,
        segment: SignalSegment,
        source_tweet_id: str,
    ) -> TradeSignal:
        """Under Trade header: keywords first, else ML BUY/SELL only; never IGNORE/WATCH."""
        local = segment.local_text
        normalized = local.lower()
        buy_score = RuleBasedSignalParser._score(normalized, self._rules.buy_keywords)
        sell_score = RuleBasedSignalParser._score(normalized, self._rules.sell_keywords)

        if buy_score > sell_score and buy_score > 0:
            return self._build_sized_signal(
                raw_text=local,
                source_tweet_id=source_tweet_id,
                ticker=segment.ticker,
                action=SignalAction.BUY,
                score=buy_score,
            )
        if sell_score > buy_score and sell_score > 0:
            return self._build_sized_signal(
                raw_text=local,
                source_tweet_id=source_tweet_id,
                ticker=segment.ticker,
                action=SignalAction.SELL,
                score=sell_score,
            )

        prediction = self._classifier.predict_buy_sell(local)
        needs_review = prediction.confidence < self._trade_header_review_confidence
        return self._from_ml(
            raw_text=local,
            source_tweet_id=source_tweet_id,
            ticker=segment.ticker,
            prediction=prediction,
            needs_review=needs_review,
            review_reason="trade_header_low_conf" if needs_review else None,
        )

    def _build_sized_signal(
        self,
        *,
        raw_text: str,
        source_tweet_id: str,
        ticker: str,
        action: SignalAction,
        score: int,
    ) -> TradeSignal:
        confidence = min(0.99, 0.50 + (score * 0.08))
        strength = RuleBasedSignalParser._strength_from_score(score)
        sell_fraction = None
        target_portfolio_pct = None
        sell_sizing_explicit = False
        buy_conviction = None
        portfolio_allocation_pct = None
        if action == SignalAction.SELL:
            target_portfolio_pct = infer_target_portfolio_pct(raw_text)
            sell_sizing_explicit = has_explicit_sell_sizing(raw_text)
            if target_portfolio_pct is None:
                sell_fraction = infer_sell_fraction(
                    raw_text,
                    default_fraction=self._rules.default_sell_fraction,
                )
        elif action == SignalAction.BUY:
            buy_conviction = infer_buy_conviction(raw_text)
            portfolio_allocation_pct = infer_portfolio_allocation_pct(raw_text)

        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=action,
            confidence=confidence,
            strength=strength,
            score=score,
            raw_text=raw_text,
            suggested_trade_usd=0.0,
            sell_fraction=sell_fraction,
            target_portfolio_pct=target_portfolio_pct,
            sell_sizing_explicit=sell_sizing_explicit,
            buy_conviction=buy_conviction,
            portfolio_allocation_pct=portfolio_allocation_pct,
        )

    def _ml_usable(self, prediction: ActionPrediction) -> bool:
        if prediction.action == SignalAction.IGNORE:
            return False
        return (
            prediction.confidence >= self._ml_min_confidence
            and prediction.margin >= self._ml_min_margin
        )

    def _from_ml(
        self,
        *,
        raw_text: str,
        source_tweet_id: str,
        ticker: str,
        prediction: ActionPrediction,
        needs_review: bool = False,
        review_reason: str | None = None,
    ) -> TradeSignal:
        score = max(3, int(round(prediction.confidence * 10)))
        confidence = min(0.99, max(0.5, prediction.confidence))
        strength = RuleBasedSignalParser._strength_from_score(score)
        sell_fraction = None
        target_portfolio_pct = None
        sell_sizing_explicit = False
        buy_conviction = None
        portfolio_allocation_pct = None
        if prediction.action == SignalAction.SELL:
            target_portfolio_pct = infer_target_portfolio_pct(raw_text)
            sell_sizing_explicit = has_explicit_sell_sizing(raw_text)
            if target_portfolio_pct is None:
                sell_fraction = infer_sell_fraction(
                    raw_text,
                    default_fraction=self._rules.default_sell_fraction,
                )
            else:
                sell_fraction = None
        elif prediction.action == SignalAction.BUY:
            buy_conviction = infer_buy_conviction(raw_text)
            portfolio_allocation_pct = infer_portfolio_allocation_pct(raw_text)

        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=prediction.action,
            confidence=confidence,
            strength=strength,
            score=score,
            raw_text=raw_text,
            suggested_trade_usd=0.0,
            sell_fraction=sell_fraction,
            target_portfolio_pct=target_portfolio_pct,
            sell_sizing_explicit=sell_sizing_explicit,
            buy_conviction=buy_conviction,
            portfolio_allocation_pct=portfolio_allocation_pct,
            needs_review=needs_review,
            review_reason=review_reason,
        )

    @property
    def known_tickers(self) -> set[str]:
        return self._rules.known_tickers
