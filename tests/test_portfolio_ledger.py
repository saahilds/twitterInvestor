from app.models.db_models import SignalAction
from app.portfolio.ledger import TradeLot, build_portfolio_ledger, trade_unit_price


def test_trade_unit_price_prefers_fill() -> None:
    assert trade_unit_price(fill_price=10.5, limit_price=11.0, amount_usd=1.0, quantity=0.1) == 10.5


def test_average_cost_buy_then_sell_realized() -> None:
    ledger = build_portfolio_ledger(
        [
            TradeLot(
                trade_id=1,
                ticker="SPY",
                action=SignalAction.BUY,
                amount_usd=10.0,
                quantity=0.1,
                fill_price=100.0,
                limit_price=100.0,
                status="filled",
                simulation=False,
            ),
            TradeLot(
                trade_id=2,
                ticker="SPY",
                action=SignalAction.BUY,
                amount_usd=10.0,
                quantity=0.1,
                fill_price=110.0,
                limit_price=110.0,
                status="filled",
                simulation=False,
            ),
            TradeLot(
                trade_id=3,
                ticker="SPY",
                action=SignalAction.SELL,
                amount_usd=12.0,
                quantity=0.1,
                fill_price=120.0,
                limit_price=120.0,
                status="filled",
                simulation=False,
            ),
        ],
        include_simulation=True,
    )
    pos = ledger.positions["SPY"]
    assert pos.shares_held == 0.1
    assert pos.avg_cost_basis == 105.0
    # Sold 0.1 @ 120 vs avg 105 => +1.5 realized
    assert pos.realized_pnl == 1.5


def test_excludes_failed_trades() -> None:
    ledger = build_portfolio_ledger(
        [
            TradeLot(
                trade_id=1,
                ticker="NVDA",
                action=SignalAction.BUY,
                amount_usd=1.0,
                quantity=None,
                fill_price=None,
                limit_price=None,
                status="failed",
                simulation=False,
            ),
        ]
    )
    assert "NVDA" not in ledger.positions
