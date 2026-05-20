from __future__ import annotations

import asyncio
import logging
import uuid

from app.config.settings import Settings
from app.models.schemas import BrokerOrderResult

try:
    from robin_stocks import robinhood as rh
except Exception:  # pragma: no cover - environment-specific
    rh = None


class RobinhoodBroker:
    """Robinhood order execution with explicit live-trading guardrails."""

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._logged_in = False

    async def buy_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return await self._place_order("buy", ticker, amount_usd)

    async def sell_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return await self._place_order("sell", ticker, amount_usd)

    async def _place_order(self, side: str, ticker: str, amount_usd: float) -> BrokerOrderResult:
        if not self.settings.live_trading_enabled:
            return BrokerOrderResult(
                status="simulated",
                order_id=f"sim-{uuid.uuid4().hex[:12]}",
                simulation=True,
                raw_response={"side": side, "ticker": ticker, "amount_usd": amount_usd},
            )

        if rh is None:
            return BrokerOrderResult(
                status="failed",
                simulation=False,
                raw_response={"error": "robin_stocks_unavailable"},
            )

        if not self._logged_in:
            login_result = await asyncio.to_thread(self._login)
            if not login_result:
                return BrokerOrderResult(
                    status="failed",
                    simulation=False,
                    raw_response={"error": "robinhood_login_failed"},
                )

        try:
            response = await asyncio.to_thread(self._submit_order, side, ticker, amount_usd)
            order_id = response.get("id") if isinstance(response, dict) else None
            return BrokerOrderResult(
                status="submitted",
                order_id=order_id,
                simulation=False,
                raw_response=response if isinstance(response, dict) else {"raw": str(response)},
            )
        except Exception as exc:
            self.logger.exception("broker_order_failed", extra={"error": str(exc), "ticker": ticker, "side": side})
            return BrokerOrderResult(
                status="failed",
                simulation=False,
                raw_response={"error": str(exc), "ticker": ticker, "side": side},
            )

    def _login(self) -> bool:
        username = self.settings.robinhood_username
        password = self.settings.robinhood_password
        if not username or not password:
            self.logger.error("missing_robinhood_credentials")
            return False

        result = rh.login(username=username, password=password, expiresIn=86400)
        self._logged_in = bool(result)
        return self._logged_in

    @staticmethod
    def _submit_order(side: str, ticker: str, amount_usd: float) -> dict:
        if side == "buy":
            response = rh.orders.order_buy_fractional_by_price(
                symbol=ticker,
                amountInDollars=amount_usd,
                timeInForce="gfd",
            )
        else:
            response = rh.orders.order_sell_fractional_by_price(
                symbol=ticker,
                amountInDollars=amount_usd,
                timeInForce="gfd",
            )
        if isinstance(response, dict):
            return response
        return {"response": response}
