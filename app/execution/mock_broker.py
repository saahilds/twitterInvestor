from __future__ import annotations

import uuid

from app.execution.order_details import enrich_broker_order_result
from app.models.schemas import BrokerOrderResult


class MockBroker:
    """Deterministic broker used in tests or forced simulation."""

    async def buy_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return self._simulated_result("buy", ticker, amount_usd=amount_usd)

    async def sell_market(
        self, ticker: str, quantity: float, *, amount_usd: float | None = None
    ) -> BrokerOrderResult:
        return self._simulated_result("sell", ticker, quantity=quantity, amount_usd=amount_usd)

    async def buy_limit_at_ask(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return self._simulated_result("buy_limit_at_ask", ticker, amount_usd=amount_usd)

    @staticmethod
    def _simulated_result(
        side: str,
        ticker: str,
        *,
        amount_usd: float | None = None,
        quantity: float | None = None,
    ) -> BrokerOrderResult:
        simulated_ask = 50.0
        if side == "sell":
            if quantity is None or quantity <= 0:
                raise ValueError("sell_market requires quantity")
            resolved_qty = quantity
            resolved_amount = amount_usd if amount_usd is not None else round(quantity * simulated_ask, 2)
        else:
            if amount_usd is None:
                raise ValueError("buy orders require amount_usd")
            resolved_qty = round(amount_usd / simulated_ask, 6)
            resolved_amount = amount_usd
        order_type = "limit_at_ask" if side == "buy_limit_at_ask" else (
            "fractional_market_quantity" if side == "sell" else "fractional_market"
        )
        return enrich_broker_order_result(
            BrokerOrderResult(
                status="simulated",
                order_id=f"mock-{uuid.uuid4().hex[:10]}",
                simulation=True,
                quantity=resolved_qty,
                raw_response={
                    "side": side,
                    "ticker": ticker,
                    "amount_usd": resolved_amount,
                    "quantity": resolved_qty,
                    "order_type": order_type,
                    "ask": simulated_ask,
                    "limit_price": simulated_ask,
                },
            ),
            order_execution_mode=order_type,
        )
