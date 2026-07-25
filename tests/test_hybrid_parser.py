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


def _one(signals):
    assert len(signals) >= 1
    return signals[0]


def test_hybrid_parses_entered_new_position_as_buy() -> None:
    parser = HybridSignalParser(known_tickers=["ADEA", "AMD"])
    signals = parser.parse(ADEA_TWEET, source_tweet_id="2062197146837750012")
    buys = [s for s in signals if s.action == SignalAction.BUY]
    assert len(buys) == 1
    assert buys[0].ticker == "ADEA"
    assert buys[0].confidence >= 0.5


def test_hybrid_still_handles_took_position_and_avoids_sells_off_false_positive() -> None:
    parser = HybridSignalParser(known_tickers=["AAOI", "SPY"])
    signals = parser.parse(AAOI_TWEET, source_tweet_id="2061442067117543814")
    buys = [s for s in signals if s.action == SignalAction.BUY]
    assert len(buys) == 1
    assert buys[0].ticker == "AAOI"


def test_hybrid_ml_path_sell_sets_fraction() -> None:
    """Ambiguous sell routed through ML must not crash (_from_ml was staticmethod)."""
    parser = HybridSignalParser(
        known_tickers=["META"],
        keyword_clear_score=99,  # force ML path for sells with low keyword score
        ml_min_confidence=0.3,
        ml_min_margin=0.01,
    )
    signal = _one(parser.parse("trimmed META today", source_tweet_id="ml-sell-1"))
    assert signal.action == SignalAction.SELL
    assert signal.ticker == "META"
    assert signal.sell_fraction is not None
    assert signal.sell_fraction > 0


def test_hybrid_ignores_thesis_only_commentary() -> None:
    parser = HybridSignalParser(known_tickers=["ADEA"])
    signal = _one(
        parser.parse(
            "$ADEA owns valuable tech patents. AMD and Microsoft license them. Here is the thesis.",
            source_tweet_id="thesis-only",
        )
    )

    assert signal.action == SignalAction.IGNORE
    assert signal.ticker == "ADEA"


def test_hybrid_ignores_spy_just_puked_market_commentary() -> None:
    parser = HybridSignalParser(known_tickers=["SPY"])
    signal = _one(parser.parse("$SPY\n just puked.", source_tweet_id="spy-puked"))
    assert signal.action == SignalAction.IGNORE
    assert signal.ticker == "SPY"


def test_hybrid_still_buys_explicit_add() -> None:
    parser = HybridSignalParser(known_tickers=["NVDA"])
    signal = _one(parser.parse("adding $NVDA starter", source_tweet_id="nvda-add"))
    assert signal.action == SignalAction.BUY
    assert signal.ticker == "NVDA"
