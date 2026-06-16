from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db_models import Trade
from app.models.schemas import PortfolioPnlResponse, TickerPnlRead
from app.portfolio.ledger import PortfolioLedger, TradeLot, build_portfolio_ledger
from app.portfolio.quotes import QuoteProvider


class PnlService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        quote_provider: QuoteProvider,
        *,
        include_simulation: bool = True,
        quote_cache_seconds: int = 60,
    ) -> None:
        self.session_factory = session_factory
        self.quote_provider = quote_provider
        self.include_simulation = include_simulation
        self.quote_cache_seconds = quote_cache_seconds

    def build_report(self, *, fetch_live_prices: bool = True) -> PortfolioPnlResponse:
        with self.session_factory() as db:
            trades = db.execute(select(Trade).order_by(Trade.id.asc())).scalars().all()
            lots = [
                TradeLot(
                    trade_id=trade.id,
                    ticker=trade.ticker,
                    action=trade.action,
                    amount_usd=trade.amount_usd,
                    quantity=trade.quantity,
                    fill_price=trade.fill_price,
                    limit_price=trade.limit_price,
                    status=trade.status,
                    simulation=trade.simulation,
                )
                for trade in trades
            ]

        ledger = build_portfolio_ledger(lots, include_simulation=self.include_simulation)
        tickers = [pos.ticker for pos in ledger.positions.values() if pos.shares_held > 0 or pos.realized_pnl != 0]
        prices: dict[str, float | None] = {}
        if fetch_live_prices and tickers:
            prices = self.quote_provider.get_prices(tickers, cache_seconds=self.quote_cache_seconds)

        rows = self._rows_from_ledger(ledger, prices)
        return PortfolioPnlResponse(
            tickers=rows,
            realized_pnl_total=round(ledger.realized_pnl_total, 4),
            unrealized_pnl_total=round(sum(row.unrealized_pnl or 0.0 for row in rows), 4),
            total_pnl=round(
                ledger.realized_pnl_total + sum(row.unrealized_pnl or 0.0 for row in rows),
                4,
            ),
            include_simulation=self.include_simulation,
            prices_as_of="live" if fetch_live_prices else "none",
        )

    @staticmethod
    def _rows_from_ledger(ledger: PortfolioLedger, prices: dict[str, float | None]) -> list[TickerPnlRead]:
        rows: list[TickerPnlRead] = []
        for ticker in sorted(ledger.positions.keys()):
            pos = ledger.positions[ticker]
            if pos.shares_held <= 0 and pos.realized_pnl == 0 and pos.buy_count == 0:
                continue

            last_price = prices.get(ticker)
            market_value = (pos.shares_held * last_price) if last_price is not None else None
            cost_open = pos.cost_basis_total
            unrealized = (market_value - cost_open) if market_value is not None else None
            unrealized_pct = (
                (unrealized / cost_open) * 100.0 if unrealized is not None and cost_open > 0 else None
            )
            total = (pos.realized_pnl + unrealized) if unrealized is not None else pos.realized_pnl

            rows.append(
                TickerPnlRead(
                    ticker=ticker,
                    shares_held=round(pos.shares_held, 6),
                    avg_cost_basis=round(pos.avg_cost_basis, 4),
                    cost_basis_open=round(cost_open, 4),
                    last_price=round(last_price, 4) if last_price is not None else None,
                    market_value=round(market_value, 4) if market_value is not None else None,
                    realized_pnl=round(pos.realized_pnl, 4),
                    unrealized_pnl=round(unrealized, 4) if unrealized is not None else None,
                    unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
                    total_pnl=round(total, 4),
                    buy_count=pos.buy_count,
                    sell_count=pos.sell_count,
                )
            )

        rows.sort(key=lambda row: row.ticker)
        return rows
