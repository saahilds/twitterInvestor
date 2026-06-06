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
    assert signal.sell_fraction == 0.25


def test_parser_sell_half_fraction() -> None:
    parser = RuleBasedSignalParser(known_tickers=["NVDA"])
    signal = parser.parse("sold half my $NVDA", source_tweet_id="half")

    assert signal.action == SignalAction.SELL
    assert signal.sell_fraction == 0.5


def test_parser_parses_cashtag_not_on_allowlist_for_risk_layer() -> None:
    parser = RuleBasedSignalParser(known_tickers=["AAPL"])
    signal = parser.parse("adding $XYZ", source_tweet_id="3")

    assert signal.action == SignalAction.BUY
    assert signal.ticker == "XYZ"


def test_parser_ignores_bare_symbol_not_on_allowlist() -> None:
    parser = RuleBasedSignalParser(known_tickers=["AAPL"])
    signal = parser.parse("adding XYZ", source_tweet_id="3")

    assert signal.action == SignalAction.IGNORE
    assert signal.ticker is None


AAOI_TWEET = """My pick for the full port challenge is...

Just took the position.


$AAOI


Here is the setup.


$AAOI
 is sitting roughly 30% off its all time highs and the reason is dilution overhang. Every time this company raises capital the stock sells off as the market digests new shares."""


def test_parser_prefers_action_cashtag_over_allowlisted_thesis_symbol() -> None:
    parser = RuleBasedSignalParser(known_tickers=["AMD", "MSFT"], default_trade_size_usd=1.0)
    text = (
        "adding $ZZZZ starter. Here is the thesis. "
        "$AMD and $MSFT license patents from $ZZZZ."
    )
    signal = parser.parse(text, source_tweet_id="unlisted-entry")

    assert signal.ticker == "ZZZZ"
    assert signal.action == SignalAction.BUY


def test_parser_took_position_is_buy_not_sells_off_false_positive() -> None:
    parser = RuleBasedSignalParser(known_tickers=["AAOI", "SPY"], default_trade_size_usd=1.0)
    signal = parser.parse(AAOI_TWEET, source_tweet_id="2061442067117543814")

    assert signal.action == SignalAction.BUY
    assert signal.ticker == "AAOI"
    assert signal.score >= 4
