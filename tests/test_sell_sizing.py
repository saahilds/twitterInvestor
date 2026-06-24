from app.execution.holdings import BrokerHolding
from app.risk.sell_sizing import resolve_sell_notional_usd


def _holding(*, quantity: float = 10.0, last_price: float = 50.0) -> BrokerHolding:
    return BrokerHolding(
        ticker="ADEA",
        quantity=quantity,
        average_cost=40.0,
        last_price=last_price,
        market_value=quantity * last_price,
        cost_basis=quantity * 40.0,
        unrealized_pnl=100.0,
        unrealized_pnl_pct=25.0,
    )


def test_sell_half_of_position_value() -> None:
    amount = resolve_sell_notional_usd(_holding(), 0.5, max_trade_size_usd=10_000)
    assert amount == 248.75


def test_sell_capped_by_max_trade_size_on_partial() -> None:
    amount = resolve_sell_notional_usd(_holding(), 0.5, max_trade_size_usd=100.0)
    assert amount == 100.0


def test_sell_full_position_not_capped_by_max_trade_size() -> None:
    amount = resolve_sell_notional_usd(_holding(), 1.0, max_trade_size_usd=100.0)
    assert amount == 500.0 * 0.995


def test_sell_order_includes_quantity() -> None:
    from app.risk.sell_sizing import resolve_sell_order

    order = resolve_sell_order(_holding(quantity=4.0, last_price=25.0), 1.0, max_trade_size_usd=100.0)
    assert order is not None
    assert order.quantity == 3.98
    assert order.amount_usd > 0


def test_sell_zero_fraction_returns_none() -> None:
    assert resolve_sell_notional_usd(_holding(), 0.0, max_trade_size_usd=100.0) is None
