from app.parsing.buy_intent import is_affirmative_buy_intent


def test_rejects_spy_just_puked() -> None:
    assert not is_affirmative_buy_intent("$SPY just puked.")
    assert not is_affirmative_buy_intent("$SPY\n just puked.")


def test_rejects_price_reaction_slang() -> None:
    assert not is_affirmative_buy_intent("$QQQ dumped hard")
    assert not is_affirmative_buy_intent("$NVDA ripped")
    assert not is_affirmative_buy_intent("$TSLA tanked today")
    assert not is_affirmative_buy_intent("$META melting")
    assert not is_affirmative_buy_intent("$AMD crushed after earnings")


def test_accepts_explicit_buy_alerts() -> None:
    assert is_affirmative_buy_intent("adding $NVDA starter")
    assert is_affirmative_buy_intent("i just entered $ADEA at a 5% weight")
    assert is_affirmative_buy_intent("took the position in $AAOI")
    assert is_affirmative_buy_intent("Added 2% port in $RDDT for a swing")
    assert is_affirmative_buy_intent("Upsized $INTC to a 6% position.")


def test_accepts_buy_even_if_price_reaction_also_mentioned() -> None:
    assert is_affirmative_buy_intent("$SPY puked so I added a starter")


def test_rejects_tempting_without_entry() -> None:
    assert not is_affirmative_buy_intent(
        "i most likely will not be making any moves because i'm leveraged already. "
        "but this $AAOI dip is very very tempting"
    )
