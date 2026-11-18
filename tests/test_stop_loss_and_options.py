from app.models.db_models import SignalAction
from app.parsing.buy_intent import is_affirmative_buy_intent
from app.parsing.hybrid_signal_parser import HybridSignalParser
from app.parsing.options_intent import is_options_contract_language
from app.parsing.sell_intent import is_affirmative_sell_intent
from app.parsing.stop_loss_intent import is_stop_loss_setup


def test_stop_loss_setup_detected() -> None:
    assert is_stop_loss_setup("Trade\n\n$COHR stop loss set at $224.50")
    assert is_stop_loss_setup("Set a stop loss at $205 on $COHR.")
    assert is_stop_loss_setup("Set a stop loss for $BE at $116.")


def test_options_contract_language_detected() -> None:
    assert is_options_contract_language("Added some $NVDA $150 Puts expiring on Friday")
    assert is_options_contract_language("Sold my $SMH puts for about 85% gains")
    assert not is_options_contract_language("I wanna stay put in $NVDA for now")


def test_hybrid_ignores_stop_loss_even_with_trade_header() -> None:
    parser = HybridSignalParser(known_tickers=["COHR"])
    signals = parser.parse(
        "Trade\n\nHey guys\n\n$COHR\n stop loss set at $224.50",
        source_tweet_id="stop-1",
    )
    assert signals
    assert all(s.action == SignalAction.IGNORE for s in signals)


def test_hybrid_ignores_options_buys_and_sells_live() -> None:
    """Options are labeled BUY/SELL for training, but live equity path must IGNORE."""
    parser = HybridSignalParser(known_tickers=["NVDA", "SMH"])
    buy_puts = parser.parse(
        "Trade\n\nAdded some $NVDA $150 Puts expiring on Friday for my hedge.",
        source_tweet_id="opts-buy",
    )
    assert buy_puts
    assert all(s.action == SignalAction.IGNORE for s in buy_puts)

    sell_puts = parser.parse(
        "Sold my $SMH puts for about 85% gains here.",
        source_tweet_id="opts-sell",
    )
    assert sell_puts
    assert all(s.action == SignalAction.IGNORE for s in sell_puts)


def test_looking_for_entry_is_not_affirmative_buy() -> None:
    assert not is_affirmative_buy_intent(
        "Looking for my entry in some $NVDA puts now.\n\nWill alert when I add."
    )


def test_past_sell_recap_does_not_block_upsize_buy() -> None:
    text = (
        "Doing a little bit of upsizing here with my cash from the $NVDA sell yesterday.\n"
        "Upsized $NFLX from 3% to 4% at $63.23\n"
        "Upsized $UBER from 2% to 3% at $69.73"
    )
    assert is_affirmative_buy_intent(text)
    assert not is_affirmative_sell_intent(text)


def test_hybrid_upsize_with_prior_sell_cash_is_buy() -> None:
    parser = HybridSignalParser(known_tickers=["NVDA", "NFLX", "UBER"])
    signals = parser.parse(
        "Trade\n\nHey guys,\n\nDoing a little bit of upsizing here with my cash from the "
        "$NVDA sell yesterday.\n\nUpsized $NFLX from 3% to 4% at $63.23\n"
        "Upsized $UBER from 2% to 3% at $69.73",
        source_tweet_id="upsize-cash",
    )
    by_ticker = {s.ticker: s.action for s in signals}
    assert by_ticker.get("NFLX") == SignalAction.BUY
    assert by_ticker.get("UBER") == SignalAction.BUY


def test_hybrid_swap_sold_for_is_sell_then_buy() -> None:
    parser = HybridSignalParser(known_tickers=["LITE", "COHR"])
    signals = parser.parse(
        "Trade\n\nSold $LITE\n\nSwapped for 5% $COHR at $223.58.\n\nSame sector.",
        source_tweet_id="swap-1",
    )
    by_ticker = {s.ticker: s.action for s in signals}
    assert by_ticker.get("LITE") == SignalAction.SELL
    assert by_ticker.get("COHR") == SignalAction.BUY
