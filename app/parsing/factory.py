from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from app.config.settings import Settings
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.signal_parser import RuleBasedSignalParser


class SignalParser(Protocol):
    known_tickers: set[str]
    default_trade_size_usd: float

    def parse(
        self,
        text: str,
        source_tweet_id: str,
        *,
        extra_known_tickers: Iterable[str] | None = None,
    ): ...


def build_signal_parser(settings: Settings) -> RuleBasedSignalParser | HybridSignalParser:
    known = settings.allowed_tickers
    default_size = settings.default_trade_size_usd
    sell_fraction_default = settings.default_sell_fraction
    if settings.signal_parser_backend == "keywords":
        return RuleBasedSignalParser(
            known_tickers=known,
            default_trade_size_usd=default_size,
            default_sell_fraction=sell_fraction_default,
        )
    return HybridSignalParser(
        known_tickers=known,
        default_trade_size_usd=default_size,
        default_sell_fraction=sell_fraction_default,
        ml_min_confidence=settings.signal_ml_min_confidence,
        ml_min_margin=settings.signal_ml_min_margin,
    )
