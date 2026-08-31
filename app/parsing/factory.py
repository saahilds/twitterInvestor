from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from app.config.settings import Settings
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.signal_parser import RuleBasedSignalParser


class SignalParser(Protocol):
    known_tickers: set[str]

    def parse(
        self,
        text: str,
        source_tweet_id: str,
        *,
        extra_known_tickers: Iterable[str] | None = None,
    ): ...


def build_signal_parser(settings: Settings) -> RuleBasedSignalParser | HybridSignalParser:
    # Parser hints only: cashtags always parse, and no ticker is blocked from trading.
    known = settings.known_tickers
    sell_fraction_default = settings.default_sell_fraction
    if settings.signal_parser_backend == "keywords":
        return RuleBasedSignalParser(
            known_tickers=known,
            default_sell_fraction=sell_fraction_default,
        )
    from app.parsing.ml_action_classifier import ActionClassifier

    classifier = ActionClassifier.load() or ActionClassifier.train()
    return HybridSignalParser(
        known_tickers=known,
        default_sell_fraction=sell_fraction_default,
        action_classifier=classifier,
        ml_min_confidence=settings.signal_ml_min_confidence,
        ml_min_margin=settings.signal_ml_min_margin,
        trade_header_review_confidence=settings.signal_trade_header_review_confidence,
    )
