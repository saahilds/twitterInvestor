from __future__ import annotations

from collections.abc import Iterable

from app.models.db_models import SignalAction
from app.models.schemas import TradeSignal
from app.parsing.ml_action_classifier import ActionClassifier, ActionPrediction
from app.parsing.sell_fraction import infer_sell_fraction
from app.parsing.signal_parser import RuleBasedSignalParser


class HybridSignalParser:
    """Rules for ticker extraction; keywords + small ML model for buy/sell/ignore."""

    def __init__(
        self,
        known_tickers: Iterable[str],
        default_trade_size_usd: float = 1.0,
        default_sell_fraction: float = 1.0,
        *,
        action_classifier: ActionClassifier | None = None,
        ml_min_confidence: float = 0.42,
        ml_min_margin: float = 0.08,
        keyword_clear_score: int = 3,
    ) -> None:
        self._rules = RuleBasedSignalParser(
            known_tickers=known_tickers,
            default_trade_size_usd=default_trade_size_usd,
            default_sell_fraction=default_sell_fraction,
        )
        self._classifier = action_classifier or ActionClassifier.train()
        self._ml_min_confidence = ml_min_confidence
        self._ml_min_margin = ml_min_margin
        self._keyword_clear_score = keyword_clear_score

    def parse(
        self,
        text: str,
        source_tweet_id: str,
        *,
        extra_known_tickers: Iterable[str] | None = None,
    ) -> TradeSignal:
        raw_text = text.strip()
        rule_signal = self._rules.parse(
            raw_text,
            source_tweet_id,
            extra_known_tickers=extra_known_tickers,
        )
        if rule_signal.ticker is None:
            return rule_signal

        keyword_action = (
            rule_signal.action
            if rule_signal.action != SignalAction.IGNORE
            and rule_signal.score >= self._keyword_clear_score
            else None
        )
        if keyword_action is not None:
            return rule_signal

        ml_prediction = self._classifier.predict(raw_text)
        if self._ml_usable(ml_prediction):
            return self._from_ml(
                raw_text=raw_text,
                source_tweet_id=source_tweet_id,
                ticker=rule_signal.ticker,
                prediction=ml_prediction,
                suggested_trade_usd=self._rules.default_trade_size_usd,
            )

        if rule_signal.action != SignalAction.IGNORE:
            return rule_signal

        return rule_signal

    def _ml_usable(self, prediction: ActionPrediction) -> bool:
        if prediction.action == SignalAction.IGNORE:
            return False
        return (
            prediction.confidence >= self._ml_min_confidence
            and prediction.margin >= self._ml_min_margin
        )

    @staticmethod
    def _from_ml(
        *,
        raw_text: str,
        source_tweet_id: str,
        ticker: str,
        prediction: ActionPrediction,
        suggested_trade_usd: float,
    ) -> TradeSignal:
        score = max(3, int(round(prediction.confidence * 10)))
        confidence = min(0.99, max(0.5, prediction.confidence))
        strength = RuleBasedSignalParser._strength_from_score(score)
        sell_fraction = None
        if prediction.action == SignalAction.SELL:
            sell_fraction = infer_sell_fraction(
                raw_text,
                default_fraction=self._rules.default_sell_fraction,
            )

        return TradeSignal(
            source_tweet_id=source_tweet_id,
            ticker=ticker,
            action=prediction.action,
            confidence=confidence,
            strength=strength,
            score=score,
            raw_text=raw_text,
            suggested_trade_usd=suggested_trade_usd,
            sell_fraction=sell_fraction,
        )

    @property
    def default_trade_size_usd(self) -> float:
        return self._rules.default_trade_size_usd

    @property
    def known_tickers(self) -> set[str]:
        return self._rules.known_tickers
