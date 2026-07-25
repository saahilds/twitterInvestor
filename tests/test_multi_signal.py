from app.models.db_models import SignalAction
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.sell_fraction import infer_sell_fraction
from app.parsing.signal_parser import RuleBasedSignalParser
from app.parsing.signal_segments import segment_trade_units

MULTI_TWEET = """New positions

Hey guys,

Adding 2.1% port in 
$INTC


Also adding 3.8% port in 
$META


Trimming 
$ADEA
 from 5% to 4%

Digging into margin here so will look to trim somewhere else today.

These are some large cap stocks I like that will outweigh some of the high growth we have."""


def test_segment_multi_ticker_units() -> None:
    segments = segment_trade_units(MULTI_TWEET)
    tickers = [s.ticker for s in segments]
    assert tickers == ["INTC", "META", "ADEA"]


def test_infer_from_to_weight_trim() -> None:
    assert infer_sell_fraction("Trimming $ADEA from 5% to 4%") == 0.2


def test_multi_signal_intc_meta_adea() -> None:
    parser = HybridSignalParser(known_tickers=["INTC", "META", "ADEA"])
    signals = parser.parse(MULTI_TWEET, source_tweet_id="multi-1")
    actionable = [s for s in signals if s.action != SignalAction.IGNORE]
    by_ticker = {s.ticker: s for s in actionable}

    assert set(by_ticker) == {"INTC", "META", "ADEA"}

    assert by_ticker["INTC"].action == SignalAction.BUY
    assert by_ticker["INTC"].portfolio_allocation_pct == 2.1

    assert by_ticker["META"].action == SignalAction.BUY
    assert by_ticker["META"].portfolio_allocation_pct == 3.8

    assert by_ticker["ADEA"].action == SignalAction.SELL
    assert by_ticker["ADEA"].sell_fraction == 0.2


def test_rules_parser_multi_signal() -> None:
    parser = RuleBasedSignalParser(known_tickers=["INTC", "META", "ADEA"])
    signals = parser.parse(MULTI_TWEET, source_tweet_id="multi-rules")
    by_ticker = {s.ticker: s for s in signals if s.action != SignalAction.IGNORE}
    assert by_ticker["INTC"].action == SignalAction.BUY
    assert by_ticker["META"].action == SignalAction.BUY
    assert by_ticker["ADEA"].action == SignalAction.SELL
