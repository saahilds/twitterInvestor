from app.parsing.sell_fraction import infer_sell_fraction


def test_infer_half_position() -> None:
    assert infer_sell_fraction("sold half my $NVDA position") == 0.5


def test_infer_explicit_percent() -> None:
    assert infer_sell_fraction("trimming 25% of $META") == 0.25


def test_infer_trim_default() -> None:
    assert infer_sell_fraction("trimmed $AMD today") == 0.25


def test_infer_full_sell_default() -> None:
    assert infer_sell_fraction("closed $TSLA") == 1.0
