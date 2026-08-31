from __future__ import annotations

from unittest.mock import MagicMock

from app.models.db_models import SignalAction
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.ml_action_classifier import ActionPrediction
from app.parsing.trade_header import has_trade_header

INTC_UPSIZE = "🚨Trade🚨 Upsized $INTC to a 6% position."
INTC_UPSIZE_NO_HEADER = "Upsized $INTC to a 6% position."
TRIM_HEADER = "🚨Trade🚨 Trimmed $META after the run."
DOWNSIZE_HEADER = "🚨Trade🚨 Downsized $NVDA after the run."
DOWNSIZE_NO_HEADER = "Downsized $AMD today."
MULTI_HEADER = "🚨Trade🚨\nUpsized $INTC to a 6% position.\nAlso trimmed $META."


def _one(signals):
    assert len(signals) >= 1
    return signals[0]


def test_has_trade_header_variants() -> None:
    assert has_trade_header("🚨Trade🚨 Upsized $INTC")
    assert has_trade_header("🚨 TRADE 🚨\nUpsized $INTC")
    assert has_trade_header("Trade\nUpsized $INTC")
    assert not has_trade_header("Upsized $INTC to a 6% position.")
    assert not has_trade_header("watching trade volume on $INTC")


def test_intc_upsize_with_header_is_buy() -> None:
    parser = HybridSignalParser(known_tickers=["INTC"])
    signal = _one(parser.parse(INTC_UPSIZE, source_tweet_id="intc-1"))
    assert signal.action == SignalAction.BUY
    assert signal.ticker == "INTC"
    assert signal.portfolio_allocation_pct == 6.0
    assert signal.needs_review is False


def test_intc_upsize_without_header_is_buy_via_vocab() -> None:
    parser = HybridSignalParser(known_tickers=["INTC"])
    signal = _one(parser.parse(INTC_UPSIZE_NO_HEADER, source_tweet_id="intc-2"))
    assert signal.action == SignalAction.BUY
    assert signal.ticker == "INTC"
    assert signal.portfolio_allocation_pct == 6.0


def test_header_trim_is_sell_via_keywords() -> None:
    parser = HybridSignalParser(known_tickers=["META"])
    signal = _one(parser.parse(TRIM_HEADER, source_tweet_id="trim-1"))
    assert signal.action == SignalAction.SELL
    assert signal.ticker == "META"
    assert signal.needs_review is False


def test_downsized_with_and_without_header_is_sell() -> None:
    parser = HybridSignalParser(known_tickers=["NVDA", "AMD"])
    with_header = _one(parser.parse(DOWNSIZE_HEADER, source_tweet_id="ds-1"))
    without = _one(parser.parse(DOWNSIZE_NO_HEADER, source_tweet_id="ds-2"))
    assert with_header.action == SignalAction.SELL
    assert with_header.ticker == "NVDA"
    assert without.action == SignalAction.SELL
    assert without.ticker == "AMD"


def test_header_clear_keyword_no_review_flag() -> None:
    parser = HybridSignalParser(known_tickers=["INTC"])
    signal = _one(parser.parse(INTC_UPSIZE, source_tweet_id="kw-clear"))
    assert signal.action == SignalAction.BUY
    assert signal.needs_review is False
    assert signal.review_reason is None


def test_header_weak_ml_flags_needs_review() -> None:
    classifier = MagicMock()
    classifier.predict_buy_sell.return_value = ActionPrediction(
        action=SignalAction.BUY,
        confidence=0.41,
        margin=0.05,
    )
    parser = HybridSignalParser(
        known_tickers=["XYZ"],
        action_classifier=classifier,
        trade_header_review_confidence=0.5,
    )
    # No buy/sell keywords → ML force-path.
    signal = _one(
        parser.parse("🚨Trade🚨 $XYZ looking interesting here", source_tweet_id="ml-weak")
    )
    assert signal.action == SignalAction.BUY
    assert signal.ticker == "XYZ"
    assert signal.needs_review is True
    assert signal.review_reason == "trade_header_low_conf"
    classifier.predict_buy_sell.assert_called()


def test_header_strong_ml_no_review_flag() -> None:
    classifier = MagicMock()
    classifier.predict_buy_sell.return_value = ActionPrediction(
        action=SignalAction.SELL,
        confidence=0.72,
        margin=0.2,
    )
    parser = HybridSignalParser(
        known_tickers=["XYZ"],
        action_classifier=classifier,
        trade_header_review_confidence=0.5,
    )
    signal = _one(
        parser.parse("🚨Trade🚨 $XYZ looking interesting here", source_tweet_id="ml-strong")
    )
    assert signal.action == SignalAction.SELL
    assert signal.needs_review is False
    assert signal.review_reason is None


def test_multi_ticker_header_one_action_per_ticker() -> None:
    parser = HybridSignalParser(known_tickers=["INTC", "META"])
    signals = parser.parse(MULTI_HEADER, source_tweet_id="multi-1")
    by_ticker = {s.ticker: s for s in signals if s.action != SignalAction.IGNORE}
    assert set(by_ticker) == {"INTC", "META"}
    assert by_ticker["INTC"].action == SignalAction.BUY
    assert by_ticker["META"].action == SignalAction.SELL
    assert by_ticker["INTC"].portfolio_allocation_pct == 6.0
    assert by_ticker["INTC"].needs_review is False
    assert by_ticker["META"].needs_review is False
