from __future__ import annotations

import uuid

from app.execution.order_details import enrich_broker_order_result
from app.models.schemas import BrokerOrderResult


class MockBroker:
    """Deterministic broker used in tests or forced simulation."""

    async def buy_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return self._simulated_result("buy", ticker, amount_usd)

    async def sell_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return self._simulated_result("sell", ticker, amount_usd)

    async def buy_limit_at_ask(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return self._simulated_result("buy_limit_at_ask", ticker, amount_usd)

    @staticmethod
    def _simulated_result(side: str, ticker: str, amount_usd: float) -> BrokerOrderResult:
        simulated_ask = 50.0
        quantity = round(amount_usd / simulated_ask, 6)
        order_type = "limit_at_ask" if side == "buy_limit_at_ask" else "fractional_market"
        return enrich_broker_order_result(
            BrokerOrderResult(
                status="simulated",
                order_id=f"mock-{uuid.uuid4().hex[:10]}",
                simulation=True,
                quantity=quantity,
                raw_response={
                    "side": side,
                    "ticker": ticker,
                    "amount_usd": amount_usd,
                    "order_type": order_type,
                    "ask": simulated_ask,
                    "limit_price": simulated_ask,
                },
            ),
            order_execution_mode=order_type,
        )
