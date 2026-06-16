from app.models.db_models import SignalAction
from app.parsing.hybrid_signal_parser import HybridSignalParser
from test_parser import AAOI_TWEET

ADEA_TWEET = """New position for the subs.

This is my trading account not the all in.

I just entered
$ADEA
 at a 5% weight.

Here is the thesis.


$ADEA
 owns valuable tech patents and charges the biggest companies on earth to use them. AMD, Microsoft, Google, Disney, Samsung, MLB."""


def test_hybrid_parses_entered_new_position_as_buy() -> None:
    parser = HybridSignalParser(known_tickers=["ADEA", "AMD"], default_trade_size_usd=100.0)
    signal = parser.parse(ADEA_TWEET, source_tweet_id="2062197146837750012")

    assert signal.action == SignalAction.BUY
    assert signal.ticker == "ADEA"
    assert signal.confidence >= 0.5


def test_hybrid_still_handles_took_position_and_avoids_sells_off_false_positive() -> None:
    parser = HybridSignalParser(known_tickers=["AAOI", "SPY"], default_trade_size_usd=1.0)
    signal = parser.parse(AAOI_TWEET, source_tweet_id="2061442067117543814")

    assert signal.action == SignalAction.BUY
    assert signal.ticker == "AAOI"


def test_hybrid_ignores_thesis_only_commentary() -> None:
    parser = HybridSignalParser(known_tickers=["ADEA"], default_trade_size_usd=1.0)
    signal = parser.parse(
        "$ADEA owns valuable tech patents. AMD and Microsoft license them. Here is the thesis.",
        source_tweet_id="thesis-only",
    )

    assert signal.action == SignalAction.IGNORE
    assert signal.ticker == "ADEA"
