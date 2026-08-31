from app.parsing.sell_fraction import (
    has_explicit_sell_sizing,
    infer_sell_fraction,
    infer_target_portfolio_pct,
)


def test_infer_half_position() -> None:
    assert infer_sell_fraction("sold half my $NVDA position") == 0.5


def test_infer_explicit_percent() -> None:
    assert infer_sell_fraction("trimming 25% of $META") == 0.25


def test_infer_trim_default() -> None:
    assert infer_sell_fraction("trimmed $AMD today") == 0.25
    assert has_explicit_sell_sizing("trimmed $AMD today") is False


def test_infer_from_to_portfolio_weight() -> None:
    assert infer_sell_fraction("Trimming $ADEA from 5% to 4%") == 0.2
    assert infer_target_portfolio_pct("Trimming $ADEA from 5% to 4%") is None
    assert has_explicit_sell_sizing("Trimming $ADEA from 5% to 4%") is True


def test_infer_down_to_target_weight() -> None:
    assert infer_target_portfolio_pct("Trimmed $VPG down to 2.1% trimmed at $68.66") == 2.1
    assert infer_target_portfolio_pct("Trimmed $PENG down to 2.4%") == 2.4
    assert infer_target_portfolio_pct("trim to 3% of $FOO") == 3.0
    # Fraction helper must not treat 2.1% as sell 2.1% of shares
    assert infer_sell_fraction("Trimmed $VPG down to 2.1%") == 0.25


def test_infer_full_sell_default() -> None:
    assert infer_sell_fraction("closed $TSLA") == 1.0
    assert has_explicit_sell_sizing("closed $TSLA") is True
