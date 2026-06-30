from app.models.db_models import SignalAction
from app.parsing.hybrid_signal_parser import HybridSignalParser


def test_hybrid_watch_signal_for_favorite_long() -> None:
    parser = HybridSignalParser(known_tickers=["QCOM"], default_trade_size_usd=100.0)
    signal = parser.parse(
        "$QCOM is one of my favorite longs right now and the setup is simple.",
        source_tweet_id="watch-qcom",
    )
    assert signal.action == SignalAction.WATCH
    assert signal.ticker == "QCOM"
    assert signal.watch_conviction is not None


def test_hybrid_buy_for_swing_port_add() -> None:
    parser = HybridSignalParser(known_tickers=["RDDT"], default_trade_size_usd=100.0)
    signal = parser.parse(
        "Small swing trade. Added 2% port in $RDDT for a swing. Filled at $169.10",
        source_tweet_id="buy-rddt",
    )
    assert signal.action == SignalAction.BUY
    assert signal.ticker == "RDDT"


def test_hybrid_preemptive_sell() -> None:
    parser = HybridSignalParser(known_tickers=["ASTS"], default_trade_size_usd=100.0)
    signal = parser.parse(
        "$ASTS is cooking. Going to sell before end of day and take losses.",
        source_tweet_id="sell-asts",
    )
    assert signal.action == SignalAction.SELL
    assert signal.ticker == "ASTS"


def test_hybrid_watch_for_historical_recap() -> None:
    parser = HybridSignalParser(known_tickers=["KRKNF"], default_trade_size_usd=100.0)
    text = (
        "I covered $KRKNF at $3 and eventually sold at $7.\n"
        "But at $4.40.\n"
        "It is starting to look attractive again."
    )
    signal = parser.parse(text, source_tweet_id="watch-krknf")
    assert signal.action == SignalAction.WATCH
    assert signal.ticker == "KRKNF"
    assert signal.watch_conviction is not None
