from app.execution.holdings import parse_position_row


def test_parse_position_row() -> None:
    row = parse_position_row(
        ticker="aaoi",
        quantity=10,
        average_cost=25.0,
        last_price=30.0,
    )
    assert row.ticker == "AAOI"
    assert row.market_value == 300.0
    assert row.cost_basis == 250.0
    assert row.unrealized_pnl == 50.0
    assert row.unrealized_pnl_pct == 20.0
