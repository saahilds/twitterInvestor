from app.parsing.sell_intent import is_affirmative_sell_intent


def test_affirmative_sold_all() -> None:
    assert is_affirmative_sell_intent("All In Update\n\nSold all $ASTS and took the L.")


def test_rejects_future_tense_sell() -> None:
    text = "Will possibly look to sell today depending on price, I think it is close to bottomed out $ASTS"
    assert not is_affirmative_sell_intent(text)


def test_accepts_preemptive_sell() -> None:
    text = "Going to sell before end of day and take losses on $ASTS"
    assert is_affirmative_sell_intent(text)


def test_rejects_historical_recap_with_watch() -> None:
    text = (
        "I covered $KRKNF at $3 and eventually sold at $7.\n"
        "But at $4.40.\n"
        "It is starting to look attractive again."
    )
    assert not is_affirmative_sell_intent(text)


def test_rejects_people_sell_commentary() -> None:
    text = "$ASTS down 8% today as people sell the launch news."
    assert not is_affirmative_sell_intent(text)


def test_rejects_havent_sold_commentary() -> None:
    text = "For anyone asking about $HLIT. I haven't sold a single share, nothing within the thesis has changed."
    assert not is_affirmative_sell_intent(text)


def test_accepts_trimmed() -> None:
    assert is_affirmative_sell_intent("trimmed META today")
