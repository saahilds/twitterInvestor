from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config.account_managers import AccountManagerConfig
from app.config.settings import Settings
from app.execution.broker import Broker
from app.execution.holdings import BrokerHolding
from app.execution.robinhood_broker import RobinhoodBroker
from app.models.db_models import ParsedSignal, SignalAction
from app.models.schemas import BrokerOrderResult, IngestedTweet, TradeSignal
from app.risk.risk_manager import RiskManager
from app.risk.sell_sizing import SellOrderSizing, resolve_sell_order
from app.services.trade_recorder import create_trade_record
from app.services.trade_status import TradeStatusSync, trade_is_terminal


@dataclass(slots=True)
class ManagerExecutionResult:
    manager_id: str
    parsed_signal_id: int | None = None
    trade_id: int | None = None
    allowed: bool = False
    rejection_reason: str | None = None


class AccountManager:
    """Per-account trading manager with its own broker and risk scope."""

    def __init__(
        self,
        config: AccountManagerConfig,
        settings: Settings,
        broker: Broker,
        risk_manager: RiskManager,
        session_factory: Callable[[], Session],
        logger: logging.Logger,
        trade_status_sync: TradeStatusSync | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.broker = broker
        self.risk_manager = risk_manager
        self.session_factory = session_factory
        self.logger = logger
        self.trade_status_sync = trade_status_sync
        self._paused = False

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def account_number(self) -> str | None:
        if isinstance(self.broker, RobinhoodBroker):
            return self.broker._account_number
        return None

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    async def evaluate_and_execute(
        self,
        signal: TradeSignal,
        tweet: IngestedTweet,
    ) -> ManagerExecutionResult:
        if self._paused or not self.config.enabled:
            return ManagerExecutionResult(
                manager_id=self.id,
                allowed=False,
                rejection_reason="manager_paused",
            )

        cash_available_usd = None
        holding: BrokerHolding | None = None
        if isinstance(self.broker, RobinhoodBroker):
            if signal.action == SignalAction.BUY:
                cash_available_usd = await asyncio.to_thread(self.broker.get_cash_available_usd)
            elif signal.action == SignalAction.SELL and signal.ticker:
                holding = await self._fetch_holding(signal.ticker)

        with self.session_factory() as db:
            risk_result = self.risk_manager.evaluate(
                signal,
                db,
                manager_id=self.id,
                cash_available_usd=cash_available_usd,
                holding=holding,
            )
            parsed_signal = ParsedSignal(
                tweet_pk=tweet.tweet_pk,
                source_tweet_id=signal.source_tweet_id,
                ticker=signal.ticker,
                action=signal.action,
                confidence=signal.confidence,
                strength=signal.strength,
                score=signal.score,
                raw_text=signal.raw_text,
                suggested_trade_usd=signal.suggested_trade_usd,
                rejection_reason=None if risk_result.allowed else risk_result.reason,
                manager_id=self.id,
            )
            db.add(parsed_signal)
            db.flush()
            parsed_signal_id = parsed_signal.id
            db.commit()

            if not risk_result.allowed:
                return ManagerExecutionResult(
                    manager_id=self.id,
                    parsed_signal_id=parsed_signal_id,
                    allowed=False,
                    rejection_reason=risk_result.reason,
                )

        trade_amount = risk_result.normalized_trade_usd or self.settings.default_trade_size_usd
        sell_quantity = risk_result.sell_quantity

        if signal.action == SignalAction.SELL:
            live_sell = await self._resolve_live_sell_order(
                signal.ticker or "",
                risk_result.sell_fraction or 1.0,
            )
            if live_sell is None:
                order_result = BrokerOrderResult(
                    status="failed",
                    simulation=not self.settings.live_trading_enabled,
                    raw_response={
                        "error": "holding_unavailable_for_sell",
                        "ticker": signal.ticker,
                    },
                )
                trade_amount = 0.0
                sell_quantity = None
            else:
                trade_amount = live_sell.amount_usd
                sell_quantity = live_sell.quantity
                if (
                    risk_result.sell_quantity is not None
                    and abs(live_sell.quantity - risk_result.sell_quantity) > 0.0001
                ):
                    self.logger.info(
                        "sell_quantity_refreshed_from_holdings",
                        extra={
                            "event_type": "sell_sizing",
                            "manager_id": self.id,
                            "ticker": signal.ticker,
                            "risk_quantity": risk_result.sell_quantity,
                            "live_quantity": live_sell.quantity,
                        },
                    )
                order_result = await self._execute_order(
                    signal.action,
                    signal.ticker or "",
                    trade_amount,
                    sell_quantity=sell_quantity,
                )
        else:
            order_result = await self._execute_order(
                signal.action,
                signal.ticker or "",
                trade_amount,
                sell_quantity=sell_quantity,
            )

        with self.session_factory() as db:
            trade = create_trade_record(
                parsed_signal_id=parsed_signal_id,
                source_tweet_id=signal.source_tweet_id,
                ticker=signal.ticker or "",
                action=signal.action,
                amount_usd=trade_amount,
                order_result=order_result,
                order_execution_mode=self.settings.order_execution_mode,
                manager_id=self.id,
            )
            db.add(trade)
            db.commit()
            db.refresh(trade)

            if (
                self.trade_status_sync is not None
                and not trade.simulation
                and trade.broker_order_id
                and not trade_is_terminal(trade.status)
            ):
                await self.trade_status_sync.refresh(db, trade)
                db.commit()
                db.refresh(trade)
                order_result_status = trade.status
                fill_price = trade.fill_price
                limit_price = trade.limit_price
            else:
                order_result_status = trade.status
                fill_price = trade.fill_price
                limit_price = trade.limit_price

        if order_result.status != "failed":
            event_type = "trade_executed" if order_result.simulation else "live_order_submitted"
            self.logger.info(
                event_type,
                extra={
                    "event_type": event_type,
                    "manager_id": self.id,
                    "tweet_id": tweet.tweet_id,
                    "ticker": signal.ticker,
                    "action": signal.action.value,
                    "amount_usd": trade_amount,
                    "status": order_result_status,
                    "simulation": order_result.simulation,
                    "broker_order_id": order_result.order_id,
                    "order_type": trade.order_type,
                    "limit_price": limit_price,
                    "fill_price": fill_price,
                    "quantity": trade.quantity,
                    "trade_id": trade.id,
                    "is_new_ticker": risk_result.is_new_ticker,
                    "sell_fraction": risk_result.sell_fraction,
                },
            )

        if order_result.status != "failed" and signal.ticker:
            with self.session_factory() as db:
                self.risk_manager.registry.register(
                    signal.ticker,
                    db,
                    manager_id=self.id,
                    source_tweet_id=signal.source_tweet_id,
                )

        return ManagerExecutionResult(
            manager_id=self.id,
            parsed_signal_id=parsed_signal_id,
            trade_id=trade.id,
            allowed=True,
        )

    async def _execute_order(
        self,
        action: SignalAction,
        ticker: str,
        amount_usd: float,
        *,
        sell_quantity: float | None = None,
    ) -> BrokerOrderResult:
        if action == SignalAction.BUY:
            if self.settings.order_execution_mode == "limit_at_ask":
                return await self.broker.buy_limit_at_ask(ticker=ticker, amount_usd=amount_usd)
            return await self.broker.buy_market(ticker=ticker, amount_usd=amount_usd)
        if action == SignalAction.SELL:
            if sell_quantity is None or sell_quantity <= 0:
                return BrokerOrderResult(
                    status="failed",
                    simulation=not self.settings.live_trading_enabled,
                    raw_response={"error": "missing_sell_quantity", "ticker": ticker},
                )
            return await self.broker.sell_market(
                ticker=ticker,
                quantity=sell_quantity,
                amount_usd=amount_usd,
            )

        raise ValueError(f"Unsupported signal action for execution: {action}")

    async def _resolve_live_sell_order(
        self,
        ticker: str,
        sell_fraction: float,
    ) -> SellOrderSizing | None:
        """Re-fetch Robinhood holdings and size the sell from the live position."""
        if not isinstance(self.broker, RobinhoodBroker):
            return None

        holding, error = await asyncio.to_thread(self.broker.get_holding, ticker)
        if error:
            self.logger.warning(
                "holdings_fetch_failed",
                extra={
                    "event_type": "holdings",
                    "manager_id": self.id,
                    "ticker": ticker.upper(),
                    "error": error,
                },
            )
            return None
        if holding is None or holding.quantity <= 0:
            return None

        return resolve_sell_order(
            holding,
            sell_fraction,
            max_trade_size_usd=self.risk_manager.config.max_trade_size_usd,
            min_trade_notional_usd=self.risk_manager.config.min_sell_notional_usd,
        )

    async def _fetch_holding(self, ticker: str) -> BrokerHolding | None:
        if not isinstance(self.broker, RobinhoodBroker):
            return None
        holding, error = await asyncio.to_thread(self.broker.get_holding, ticker)
        if error:
            self.logger.warning(
                "holdings_fetch_failed",
                extra={"event_type": "holdings", "manager_id": self.id, "ticker": ticker.upper(), "error": error},
            )
            return None
        return holding
