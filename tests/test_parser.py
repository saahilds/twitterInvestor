from app.models.db_models import SignalAction
from app.parsing.signal_parser import RuleBasedSignalParser


def test_parser_detects_buy_signal() -> None:
    parser = RuleBasedSignalParser(known_tickers=["NVDA", "TSLA"], default_trade_size_usd=1.5)
    signal = parser.parse("adding NVDA starter", source_tweet_id="1")

    assert signal.action == SignalAction.BUY
    assert signal.ticker == "NVDA"
    assert signal.suggested_trade_usd == 1.5
    assert signal.confidence > 0


def test_parser_detects_sell_signal() -> None:
    parser = RuleBasedSignalParser(known_tickers=["META"])
    signal = parser.parse("trimmed META today", source_tweet_id="2")

    assert signal.action == SignalAction.SELL
    assert signal.ticker == "META"


def test_parser_ignores_unknown_ticker() -> None:
    parser = RuleBasedSignalParser(known_tickers=["AAPL"])
    signal = parser.parse("adding XYZ", source_tweet_id="3")

    assert signal.action == SignalAction.IGNORE
    assert signal.ticker is None
