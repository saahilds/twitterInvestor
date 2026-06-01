from __future__ import annotations

import asyncio
import logging
import uuid

from app.config.settings import Settings
from app.execution.robinhood_accounts import (
    fetch_all_account_snapshots,
    load_all_accounts,
    resolve_account_number,
    summarize_account,
)
from app.execution.buying_power import fetch_robinhood_cash_available_usd
from app.execution.holdings import (
    BrokerHolding,
    BrokerPortfolioMetrics,
    fetch_portfolio_metrics,
    fetch_robinhood_holdings,
)
from app.execution.order_details import enrich_broker_order_result
from app.models.schemas import BrokerOrderResult

try:
    from robin_stocks import robinhood as rh
except Exception:  # pragma: no cover - environment-specific
    rh = None

try:
    import pyotp
except Exception:  # pragma: no cover - environment-specific
    pyotp = None


class RobinhoodBroker:
    """Robinhood order execution with explicit live-trading guardrails."""

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._logged_in = False
        self._account_number: str | None = None

    async def buy_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return await self._place_order("buy", ticker, amount_usd)

    async def sell_market(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        return await self._place_order("sell", ticker, amount_usd)

    async def buy_limit_at_ask(self, ticker: str, amount_usd: float) -> BrokerOrderResult:
        if not self.settings.live_trading_enabled:
            simulated_ask = 100.0
            quantity = round(amount_usd / simulated_ask, 6)
            return enrich_broker_order_result(
                BrokerOrderResult(
                    status="simulated",
                    order_id=f"sim-{uuid.uuid4().hex[:12]}",
                    simulation=True,
                    quantity=quantity,
                    account_number=self._account_number,
                    raw_response={
                        "order_type": "limit_at_ask",
                        "ticker": ticker,
                        "amount_usd": amount_usd,
                        "ask": simulated_ask,
                        "limit_price": simulated_ask,
                    },
                ),
                order_execution_mode="limit_at_ask",
            )

        if rh is None:
            return BrokerOrderResult(
                status="failed",
                simulation=False,
                raw_response={"error": "robin_stocks_unavailable"},
            )

        login_error = await asyncio.to_thread(self._ensure_live_session)
        if login_error:
            return BrokerOrderResult(status="failed", simulation=False, raw_response={"error": login_error})

        try:
            response = await asyncio.to_thread(self._submit_limit_buy_at_ask, ticker, amount_usd)
            order_id = response.get("order_id")
            return enrich_broker_order_result(
                BrokerOrderResult(
                    status=response.get("status", "submitted"),
                    order_id=order_id,
                    simulation=False,
                    quantity=response.get("quantity"),
                    account_number=response.get("account_number"),
                    raw_response=response,
                ),
                order_execution_mode="limit_at_ask",
            )
        except ValueError as exc:
            self.logger.warning(
                "live_order_rejected",
                extra={"error": str(exc), "ticker": ticker, "amount_usd": amount_usd},
            )
            return enrich_broker_order_result(
                BrokerOrderResult(
                    status="failed",
                    simulation=False,
                    raw_response={"error": str(exc), "ticker": ticker, "order_type": "limit_at_ask"},
                ),
                order_execution_mode="limit_at_ask",
            )
        except Exception as exc:
            self.logger.exception("broker_order_failed", extra={"error": str(exc), "ticker": ticker})
            return enrich_broker_order_result(
                BrokerOrderResult(
                    status="failed",
                    simulation=False,
                    raw_response={"error": str(exc), "ticker": ticker, "order_type": "limit_at_ask"},
                ),
                order_execution_mode="limit_at_ask",
            )

    def fetch_order_info(self, order_id: str) -> dict | None:
        if rh is None or not order_id:
            return None
        if not self._login():
            return None
        info = rh.orders.get_stock_order_info(order_id)
        if isinstance(info, list) and info:
            info = info[0]
        return info if isinstance(info, dict) else None

    def get_cash_available_usd(self) -> float | None:
        if rh is None:
            return None
        login_error = self._ensure_live_session()
        if login_error:
            return None
        return fetch_robinhood_cash_available_usd(self._account_number)

    def get_holdings(self) -> tuple[list[BrokerHolding], str | None]:
        if rh is None:
            return [], "robin_stocks_unavailable"
        login_error = self._ensure_live_session()
        if login_error:
            return [], login_error
        return fetch_robinhood_holdings(self._account_number)

    def get_portfolio_metrics(self) -> BrokerPortfolioMetrics:
        if rh is None:
            return BrokerPortfolioMetrics(None, None, None, None, None)
        login_error = self._ensure_live_session()
        if login_error:
            return BrokerPortfolioMetrics(None, None, None, None, None)
        return fetch_portfolio_metrics(self._account_number)

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

        login_error = await asyncio.to_thread(self._ensure_live_session)
        if login_error:
            return BrokerOrderResult(status="failed", simulation=False, raw_response={"error": login_error})

        try:
            response = await asyncio.to_thread(self._submit_order, side, ticker, amount_usd)
            order_id = response.get("id") if isinstance(response, dict) else None
            mode = "fractional_market"
            return enrich_broker_order_result(
                BrokerOrderResult(
                    status="submitted",
                    order_id=order_id,
                    simulation=False,
                    account_number=self._account_number,
                    raw_response={
                        "order_type": mode,
                        "side": side,
                        "ticker": ticker,
                        "amount_usd": amount_usd,
                        "broker_response": response if isinstance(response, dict) else {"raw": str(response)},
                    },
                ),
                order_execution_mode=mode,
            )
        except Exception as exc:
            self.logger.exception("broker_order_failed", extra={"error": str(exc), "ticker": ticker, "side": side})
            return enrich_broker_order_result(
                BrokerOrderResult(
                    status="failed",
                    simulation=False,
                    raw_response={"error": str(exc), "ticker": ticker, "side": side, "order_type": "fractional_market"},
                ),
                order_execution_mode="fractional_market",
            )

    async def verify_login(self) -> dict:
        """Log in to Robinhood and fetch account profile (no order). Works outside market hours."""
        if rh is None:
            return {"ok": False, "error": "robin_stocks_unavailable"}

        if not self.settings.robinhood_username or not self.settings.robinhood_password:
            return {"ok": False, "error": "missing_robinhood_credentials"}

        login_ok = await asyncio.to_thread(self._login)
        if not login_ok:
            return {"ok": False, "error": "robinhood_login_failed"}

        try:
            profile = await asyncio.to_thread(self._load_login_profile)
            return {"ok": True, **profile}
        except Exception as exc:
            self.logger.exception("robinhood_profile_fetch_failed", extra={"error": str(exc)})
            return {"ok": False, "error": str(exc), "logged_in": True}

    def list_accounts(self) -> list[dict]:
        if rh is None:
            return []
        if not self._logged_in and not self._login():
            return []
        return [summarize_account(account) for account in load_all_accounts()]

    async def verify_all_accounts(self) -> dict:
        """Log in once and fetch balance snapshots for every linked brokerage account."""
        if rh is None:
            return {"ok": False, "error": "robin_stocks_unavailable"}

        if not self.settings.robinhood_username or not self.settings.robinhood_password:
            return {"ok": False, "error": "missing_robinhood_credentials"}

        login_ok = await asyncio.to_thread(self._login)
        if not login_ok:
            return {"ok": False, "error": "robinhood_login_failed"}

        try:
            user = await asyncio.to_thread(rh.profiles.load_user_profile)
            if isinstance(user, list) and user:
                user = user[0]
            username = user.get("username") if isinstance(user, dict) else None

            accounts = await asyncio.to_thread(load_all_accounts)
            snapshots = await asyncio.to_thread(fetch_all_account_snapshots, accounts)
            all_ok = bool(snapshots) and all(row.get("ok") for row in snapshots)
            return {
                "ok": all_ok,
                "username": username,
                "account_count": len(snapshots),
                "accounts": snapshots,
                "robinhood_account_setting": self.settings.robinhood_account,
            }
        except Exception as exc:
            self.logger.exception("robinhood_verify_all_accounts_failed", extra={"error": str(exc)})
            return {"ok": False, "error": str(exc), "logged_in": True}

    def _load_login_profile(self) -> dict:
        session_error = self._ensure_live_session()
        if session_error:
            raise ValueError(session_error)

        user = rh.profiles.load_user_profile()
        if isinstance(user, list) and user:
            user = user[0]
        username = user.get("username") if isinstance(user, dict) else None

        accounts = load_all_accounts()
        selected = next(
            (summarize_account(account) for account in accounts if account.get("account_number") == self._account_number),
            None,
        )

        return {
            "username": username,
            "account_number": self._account_number,
            "robinhood_account": self.settings.robinhood_account,
            "selected_account": selected,
            "available_accounts": [summarize_account(account) for account in accounts],
        }

    def _ensure_live_session(self) -> str | None:
        if not self._login():
            return "robinhood_login_failed"
        try:
            self._account_number = resolve_account_number(
                self.settings.robinhood_account,
                load_all_accounts(),
            )
        except ValueError as exc:
            return str(exc)
        return None

    def _login(self) -> bool:
        username = self.settings.robinhood_username
        password = self.settings.robinhood_password
        if not username or not password:
            self.logger.error("missing_robinhood_credentials")
            return False

        mfa_code = None
        if self.settings.robinhood_mfa_secret:
            if pyotp is None:
                self.logger.error("pyotp_unavailable_for_mfa")
                return False
            mfa_code = pyotp.TOTP(self.settings.robinhood_mfa_secret).now()

        result = rh.login(
            username=username,
            password=password,
            mfa_code=mfa_code,
            expiresIn=86400,
        )
        self._logged_in = bool(result)
        return self._logged_in

    def _submit_limit_buy_at_ask(self, ticker: str, amount_usd: float) -> dict:
        symbol = ticker.upper()
        ask = self._fetch_ask_price(symbol)
        if ask <= 0:
            raise ValueError(f"invalid_ask_price:{symbol}")

        quantity = round(amount_usd / ask, 6)
        if quantity <= 0:
            raise ValueError("below_minimum_notional")

        limit_price = rh.stocks.round_price(ask)
        response = rh.orders.order_buy_limit(
            symbol=symbol,
            quantity=quantity,
            limitPrice=limit_price,
            account_number=self._account_number,
            timeInForce="gfd",
            extendedHours=False,
        )

        order_id = None
        if isinstance(response, dict):
            order_id = response.get("id")
        elif isinstance(response, list) and response and isinstance(response[0], dict):
            order_id = response[0].get("id")
            response = response[0]

        payload = {
            "status": "submitted",
            "order_id": order_id,
            "order_type": "limit_at_ask",
            "ticker": symbol,
            "amount_usd": amount_usd,
            "ask": ask,
            "limit_price": limit_price,
            "quantity": quantity,
            "account_number": self._account_number,
            "broker_response": response,
        }
        self.logger.info(
            "live_order_submitted",
            extra={
                "event_type": "live_order",
                "ticker": symbol,
                "ask": ask,
                "limit_price": limit_price,
                "quantity": quantity,
                "order_id": order_id,
                "amount_usd": amount_usd,
                "account_number": self._account_number,
            },
        )
        return payload

    @staticmethod
    def _fetch_ask_price(symbol: str) -> float:
        quote = rh.stocks.get_stock_quote_by_symbol(symbol)
        if isinstance(quote, list) and quote:
            quote = quote[0]
        if isinstance(quote, dict):
            for key in ("ask_price", "askPrice", "last_trade_price", "last_extended_hours_trade_price"):
                raw = quote.get(key)
                if raw is not None:
                    return float(raw)

        pricebook = rh.stocks.get_pricebook_by_symbol(symbol)
        if isinstance(pricebook, list) and pricebook:
            pricebook = pricebook[0]
        if isinstance(pricebook, dict):
            asks = pricebook.get("asks")
            if isinstance(asks, list) and asks:
                top = asks[0]
                if isinstance(top, dict) and top.get("price") is not None:
                    return float(top["price"])

        latest = rh.stocks.get_latest_price(symbol, includeExtendedHours=False)
        if isinstance(latest, list) and latest:
            return float(latest[0])
        if isinstance(latest, str):
            return float(latest)

        raise ValueError(f"ask_price_unavailable:{symbol}")

    @staticmethod
    def _submit_order(side: str, ticker: str, amount_usd: float) -> dict:
        if side == "buy":
            response = rh.orders.order_buy_fractional_by_price(
                symbol=ticker,
                amountInDollars=amount_usd,
                account_number=self._account_number,
                timeInForce="gfd",
            )
        else:
            response = rh.orders.order_sell_fractional_by_price(
                symbol=ticker,
                amountInDollars=amount_usd,
                account_number=self._account_number,
                timeInForce="gfd",
            )
        if isinstance(response, dict):
            return response
        return {"response": response}
