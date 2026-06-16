from app.parsing.text_normalize import extract_action_snippet, normalize_for_action_model


def test_extract_action_snippet_stops_before_thesis() -> None:
    text = "I just entered $ADEA. Here is the thesis. $ADEA owns patents."
    assert "patents" not in extract_action_snippet(text).lower()
    assert "entered" in extract_action_snippet(text).lower()


def test_normalize_replaces_cashtag() -> None:
    assert "TICKER" in normalize_for_action_model("buying $NVDA")
