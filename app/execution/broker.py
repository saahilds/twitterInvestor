from __future__ import annotations

from typing import Protocol

from app.models.schemas import BrokerOrderResult


class Broker(Protocol):
    """Broker abstraction for order placement."""

    async def buy_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        ...

    async def sell_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        ...

    async def buy_limit_at_ask(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        ...
