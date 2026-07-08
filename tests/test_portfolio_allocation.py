from app.parsing.portfolio_allocation import infer_portfolio_allocation_pct


def test_infer_weight_allocation() -> None:
    assert infer_portfolio_allocation_pct("i just entered $ADEA at a 5% weight") == 5.0


def test_infer_port_allocation() -> None:
    assert infer_portfolio_allocation_pct("added an addition 2% port in $AAOI") == 2.0


def test_infer_starting_position_allocation() -> None:
    text = "with the funds i am starting a 7% $QCOM position at $209.70"
    assert infer_portfolio_allocation_pct(text) == 7.0


def test_ignores_price_move_percent() -> None:
    text = "$AAOI is sitting roughly 30% off its all time highs"
    assert infer_portfolio_allocation_pct(text) is None
