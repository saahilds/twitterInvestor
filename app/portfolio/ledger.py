from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.models.db_models import SignalAction

_COUNTABLE_STATUSES = frozenset({"filled", "simulated", "submitted", "open"})


@dataclass(slots=True)
class TradeLot:
    trade_id: int
    ticker: str
    action: SignalAction
    amount_usd: float
    quantity: float | None
    fill_price: float | None
    limit_price: float | None
    status: str
    simulation: bool


@dataclass(slots=True)
class TickerPosition:
    ticker: str
    shares_held: float = 0.0
    cost_basis_total: float = 0.0
    realized_pnl: float = 0.0
    buy_count: int = 0
    sell_count: int = 0

    @property
    def avg_cost_basis(self) -> float:
        if self.shares_held <= 0:
            return 0.0
        return self.cost_basis_total / self.shares_held


@dataclass(slots=True)
class PortfolioLedger:
    positions: dict[str, TickerPosition] = field(default_factory=dict)
    realized_pnl_total: float = 0.0

    def position(self, ticker: str) -> TickerPosition:
        key = ticker.upper()
        if key not in self.positions:
            self.positions[key] = TickerPosition(ticker=key)
        return self.positions[key]


def trade_unit_price(
    *,
    fill_price: float | None,
    limit_price: float | None,
    amount_usd: float,
    quantity: float | None,
) -> float | None:
    if fill_price is not None and fill_price > 0:
        return fill_price
    if limit_price is not None and limit_price > 0:
        return limit_price
    if quantity is not None and quantity > 0 and amount_usd > 0:
        return amount_usd / quantity
    return None


def trade_effective_quantity(*, quantity: float | None, amount_usd: float, price: float | None) -> float | None:
    if quantity is not None and quantity > 0:
        return quantity
    if price is not None and price > 0 and amount_usd > 0:
        return amount_usd / price
    return None


def build_portfolio_ledger(
    trades: Iterable[TradeLot],
    *,
    include_simulation: bool = True,
) -> PortfolioLedger:
    """Average-cost ledger: buys add shares, sells realize P&L and reduce basis."""
    ledger = PortfolioLedger()
    ordered = sorted(trades, key=lambda row: row.trade_id)

    for trade in ordered:
        if trade.status not in _COUNTABLE_STATUSES:
            continue
        if trade.simulation and not include_simulation:
            continue

        price = trade_unit_price(
            fill_price=trade.fill_price,
            limit_price=trade.limit_price,
            amount_usd=trade.amount_usd,
            quantity=trade.quantity,
        )
        qty = trade_effective_quantity(quantity=trade.quantity, amount_usd=trade.amount_usd, price=price)
        if price is None or qty is None or qty <= 0:
            continue

        pos = ledger.position(trade.ticker)
        if trade.action == SignalAction.BUY:
            pos.shares_held += qty
            pos.cost_basis_total += qty * price
            pos.buy_count += 1
            continue

        if trade.action != SignalAction.SELL:
            continue

        sell_qty = min(qty, pos.shares_held)
        if sell_qty <= 0:
            continue

        avg_cost = pos.avg_cost_basis
        realized = (price - avg_cost) * sell_qty
        pos.realized_pnl += realized
        ledger.realized_pnl_total += realized
        pos.cost_basis_total -= avg_cost * sell_qty
        pos.shares_held -= sell_qty
        pos.sell_count += 1

    return ledger
