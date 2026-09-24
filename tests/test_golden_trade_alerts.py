"""Golden regressions from production buy/sell mis-sorts."""

from __future__ import annotations

from app.models.db_models import SignalAction
from app.parsing.buy_intent import is_affirmative_buy_intent
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.sell_intent import is_affirmative_sell_intent

FPS_THESIS = """$FPS
 is up 30% since earnings and it's still one of the best growth stories in the market.

Revenue grew 94% last quarter. Bookings grew 375%. Backlog grew 256%. They booked $1.5 billion of orders in one quarter, more than they sold all year, and the book to bill went up to 3.3x.
"""

CRDO_TRIM = """Trade

Hey guys,

Sorry for the late trade but want to de risk with this position again as we added at $169 and it’s now back up.

Trimming 
$CRDO
 from 10% to 8% at $193.47.
"""

CUTTING_ASTS_KRKNF = """Trade

Hey guys,

Cutting 
$ASTS
 and $KRKNF here.

Sticking to my plan and freeing up cash.
"""

MULTI_FROM_TO = """Trade

Hey guys,

Adding 2.1% port in 
$INTC

Also adding 3.8% port in 
$META

Trimming 
$ADEA
 from 5% to 4%
"""


def _by_ticker(signals):
    return {s.ticker: s for s in signals if s.action != SignalAction.IGNORE}


def test_golden_fps_thesis_is_ignore_not_sell() -> None:
    assert not is_affirmative_sell_intent(FPS_THESIS)
    parser = HybridSignalParser(known_tickers=["FPS"])
    signal = parser.parse(FPS_THESIS, source_tweet_id="golden-fps")[0]
    assert signal.action == SignalAction.IGNORE
    assert signal.ticker == "FPS"


def test_golden_crdo_trim_is_sell_fraction_0_2() -> None:
    assert not is_affirmative_buy_intent(CRDO_TRIM)
    parser = HybridSignalParser(known_tickers=["CRDO"])
    signal = parser.parse(CRDO_TRIM, source_tweet_id="golden-crdo")[0]
    assert signal.action == SignalAction.SELL
    assert signal.ticker == "CRDO"
    assert signal.sell_fraction == 0.2
    assert signal.sell_sizing_explicit is True


def test_golden_cutting_asts_krknf_both_sell() -> None:
    parser = HybridSignalParser(known_tickers=["ASTS", "KRKNF"])
    by_ticker = _by_ticker(parser.parse(CUTTING_ASTS_KRKNF, source_tweet_id="golden-cut"))
    assert set(by_ticker) == {"ASTS", "KRKNF"}
    assert by_ticker["ASTS"].action == SignalAction.SELL
    assert by_ticker["KRKNF"].action == SignalAction.SELL


def test_golden_multi_from_to_keeps_mixed_actions() -> None:
    parser = HybridSignalParser(known_tickers=["INTC", "META", "ADEA"])
    by_ticker = _by_ticker(parser.parse(MULTI_FROM_TO, source_tweet_id="golden-multi"))
    assert by_ticker["INTC"].action == SignalAction.BUY
    assert by_ticker["META"].action == SignalAction.BUY
    assert by_ticker["ADEA"].action == SignalAction.SELL
    assert by_ticker["ADEA"].sell_fraction == 0.2


def test_golden_non_header_weak_trim_stays_ignored() -> None:
    parser = HybridSignalParser(known_tickers=["META"])
    signal = parser.parse("trimmed $META today", source_tweet_id="golden-weak-trim")[0]
    assert signal.action == SignalAction.IGNORE
