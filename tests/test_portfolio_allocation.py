from app.parsing.portfolio_allocation import infer_portfolio_allocation_pct


def test_infer_weight_allocation() -> None:
    assert infer_portfolio_allocation_pct("i just entered $ADEA at a 5% weight") == 5.0


def test_infer_port_allocation() -> None:
    assert infer_portfolio_allocation_pct("added an addition 2% port in $AAOI") == 2.0


def test_infer_starting_position_allocation() -> None:
    text = "with the funds i am starting a 7% $QCOM position at $209.70"
    assert infer_portfolio_allocation_pct(text) == 7.0


def test_infer_adding_decimal_port() -> None:
    assert infer_portfolio_allocation_pct("Adding 2.1% port in $INTC") == 2.1
    assert infer_portfolio_allocation_pct("Also adding 3.8% port in $META") == 3.8


def test_infer_with_pct_at_price() -> None:
    assert infer_portfolio_allocation_pct("Added $NFLX with a 3% position at $67.23") == 3.0
    assert infer_portfolio_allocation_pct("Added $UBER with 2% at $71.60") == 2.0
    assert infer_portfolio_allocation_pct("Added \n$HOOD\n 1% at $85.85.") == 1.0


def test_infer_ignores_pct_off_highs() -> None:
    assert infer_portfolio_allocation_pct("$NVDA is 30% off highs") is None
