from app.models.db_models import SignalAction
from app.parsing.signal_parser import RuleBasedSignalParser
from app.parsing.word_forms import inflection_forms, keyword_match_pattern


def test_inflection_forms_regular_and_irregular() -> None:
    assert inflection_forms("trim") == {"trim", "trims", "trimmed", "trimming"}
    assert inflection_forms("add") == {"add", "adds", "added", "adding"}
    assert inflection_forms("buy") == {"buy", "buys", "buying", "bought"}
    assert inflection_forms("sell") == {"sell", "sells", "selling", "sold"}
    assert inflection_forms("close") == {"closes", "closed", "closing"}
    assert "close" not in inflection_forms("close")
    assert "longer" not in inflection_forms("long")
    assert inflection_forms("long") == {"long"}


def test_keyword_match_pattern_hits_conjugations() -> None:
    pattern = keyword_match_pattern("trim")
    assert pattern.search("Trimming $CRDO from 10% to 8%")
    assert pattern.search("trimmed $META today")
    assert pattern.search("trim $AMD")
    assert not pattern.search("trampoline bounce")


def test_score_treats_verb_conjugations_as_same_signal() -> None:
    parser = RuleBasedSignalParser(known_tickers=["CRDO", "NVDA", "META"])
    sell_forms = [
        "trim $CRDO",
        "trimmed $CRDO",
        "trimming $CRDO",
        "trims $CRDO",
    ]
    sell_scores = [parser._score(text, parser.sell_keywords) for text in sell_forms]
    assert sell_scores == [3, 3, 3, 3]

    buy_forms = [
        "add $NVDA",
        "added $NVDA",
        "adding $NVDA",
        "adds $NVDA",
        "buy $META",
        "bought $META",
        "buying $META",
    ]
    buy_scores = [parser._score(text, parser.buy_keywords) for text in buy_forms]
    assert all(score == 3 for score in buy_scores)


def test_parser_conjugations_resolve_to_same_action() -> None:
    parser = RuleBasedSignalParser(known_tickers=["CRDO", "NVDA"])
    for text in ("trim $CRDO", "trimmed $CRDO", "trimming $CRDO"):
        signal = parser.parse(text, source_tweet_id="stem-sell")[0]
        assert signal.action == SignalAction.SELL
        assert signal.ticker == "CRDO"
    for text in ("add $NVDA starter", "added $NVDA starter", "adding $NVDA starter"):
        signal = parser.parse(text, source_tweet_id="stem-buy")[0]
        assert signal.action == SignalAction.BUY
        assert signal.ticker == "NVDA"
