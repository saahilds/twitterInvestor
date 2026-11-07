from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config.account_managers import AccountManagerConfig
from app.config.settings import Settings
from app.execution.broker import Broker
from app.execution.holdings import BrokerHolding
from app.execution.robinhood_broker import RobinhoodBroker
from app.models.db_models import ParsedSignal, SignalAction, Tweet
from app.models.schemas import BrokerOrderResult, IngestedTweet, TradeSignal
from app.parsing.buy_conviction import infer_buy_conviction
from app.risk.market_hours import (
    is_within_regular_market_hours,
    next_full_session_close_after,
)
from app.risk.risk_manager import RiskManager
from app.risk.sell_sizing import SellOrderSizing, resolve_sell_order
from app.services.trade_recorder import create_trade_record
from app.services.trade_status import TradeStatusSync, trade_is_terminal
from app.services.alerts import AlertService

QUEUED_FOR_MARKET_OPEN = "queued_for_market_open"
DEFERRED_QUEUED = "queued"
DEFERRED_RELEASED = "released"
DEFERRED_EXPIRED = "expired"


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
        alert_service: AlertService | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.broker = broker
        self.risk_manager = risk_manager
        self.session_factory = session_factory
        self.logger = logger
        self.trade_status_sync = trade_status_sync
        self.alert_service = alert_service
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
        *,
        as_of: datetime | None = None,
        existing_parsed_signal_id: int | None = None,
    ) -> ManagerExecutionResult:
        if self._paused or not self.config.enabled:
            return ManagerExecutionResult(
                manager_id=self.id,
                allowed=False,
                rejection_reason="manager_paused",
            )

        cash_available_usd = None
        # CK sleeve notional for % sizing — never full RH equity.
        portfolio_value_usd = self.settings.resolved_ck_portfolio_usd
        holding: BrokerHolding | None = None
        if isinstance(self.broker, RobinhoodBroker):
            if signal.action == SignalAction.BUY:
                cash_available_usd = await asyncio.to_thread(self.broker.get_cash_available_usd)
            elif signal.action == SignalAction.SELL and signal.ticker:
                holding = await self._fetch_holding(signal.ticker)

        # Default: tweet clock so a catch-up fetch at the open does not immediately
        # trade posts published outside the session — those are queued instead.
        risk_as_of = as_of if as_of is not None else tweet.posted_at

        with self.session_factory() as db:
            risk_result = self.risk_manager.evaluate(
                signal,
                db,
                manager_id=self.id,
                cash_available_usd=cash_available_usd,
                portfolio_value_usd=portfolio_value_usd,
                holding=holding,
                as_of=risk_as_of,
                exclude_parsed_signal_id=existing_parsed_signal_id,
            )
            should_queue = (
                not risk_result.allowed
                and risk_result.reason == "outside_market_hours"
                and self.settings.defer_outside_market_hours
                and signal.action in {SignalAction.BUY, SignalAction.SELL}
                and existing_parsed_signal_id is None
            )
            rejection_reason = None if risk_result.allowed else risk_result.reason
            deferred_status = None
            deferred_expires_at = None
            if should_queue:
                rejection_reason = QUEUED_FOR_MARKET_OPEN
                deferred_status = DEFERRED_QUEUED
                deferred_expires_at = next_full_session_close_after(tweet.posted_at).astimezone(
                    timezone.utc
                )

            if existing_parsed_signal_id is not None:
                parsed_signal = db.get(ParsedSignal, existing_parsed_signal_id)
                if parsed_signal is None:
                    return ManagerExecutionResult(
                        manager_id=self.id,
                        allowed=False,
                        rejection_reason="missing_deferred_signal",
                    )
                parsed_signal.suggested_trade_usd = signal.suggested_trade_usd
                parsed_signal.portfolio_allocation_pct = signal.portfolio_allocation_pct
                parsed_signal.sell_fraction = signal.sell_fraction
                parsed_signal.sell_sizing_explicit = signal.sell_sizing_explicit
                parsed_signal.target_portfolio_pct = signal.target_portfolio_pct
                if risk_result.allowed:
                    parsed_signal.rejection_reason = None
                    parsed_signal.deferred_status = DEFERRED_RELEASED
                else:
                    parsed_signal.rejection_reason = rejection_reason
                    if parsed_signal.deferred_status == DEFERRED_QUEUED:
                        parsed_signal.deferred_status = None
            else:
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
                    rejection_reason=rejection_reason,
                    watch_conviction=(
                        signal.watch_conviction.value
                        if signal.watch_conviction is not None
                        else None
                    ),
                    target_portfolio_pct=signal.target_portfolio_pct,
                    portfolio_allocation_pct=signal.portfolio_allocation_pct,
                    sell_fraction=signal.sell_fraction,
                    sell_sizing_explicit=signal.sell_sizing_explicit,
                    deferred_status=deferred_status,
                    deferred_expires_at=deferred_expires_at,
                    needs_review=signal.needs_review,
                    review_reason=signal.review_reason,
                    manager_id=self.id,
                )
                db.add(parsed_signal)
            db.flush()
            parsed_signal_id = parsed_signal.id
            db.commit()

            if not risk_result.allowed:
                if should_queue:
                    self.logger.info(
                        "signal_queued_for_market_open",
                        extra={
                            "event_type": "signal_queued",
                            "manager_id": self.id,
                            "tweet_id": tweet.tweet_id,
                            "ticker": signal.ticker,
                            "action": signal.action.value,
                            "expires_at": deferred_expires_at.isoformat()
                            if deferred_expires_at
                            else None,
                            "portfolio_allocation_pct": signal.portfolio_allocation_pct,
                        },
                    )
                    return ManagerExecutionResult(
                        manager_id=self.id,
                        parsed_signal_id=parsed_signal_id,
                        allowed=False,
                        rejection_reason=QUEUED_FOR_MARKET_OPEN,
                    )
                if signal.action not in {SignalAction.IGNORE, SignalAction.WATCH}:
                    self.logger.info(
                        "signal_rejected",
                        extra={
                            "event_type": "signal_rejected",
                            "manager_id": self.id,
                            "tweet_id": tweet.tweet_id,
                            "ticker": signal.ticker,
                            "action": signal.action.value,
                            "reason": risk_result.reason,
                            "portfolio_allocation_pct": signal.portfolio_allocation_pct,
                            "portfolio_value_usd": portfolio_value_usd,
                            "normalized_trade_usd": risk_result.normalized_trade_usd,
                        },
                    )
                    if self.alert_service and self.alert_service.should_alert_rejection(
                        risk_result.reason
                    ):
                        await self.alert_service.send(
                            "signal_rejected",
                            {
                                "manager_id": self.id,
                                "tweet_id": tweet.tweet_id,
                                "ticker": signal.ticker,
                                "action": signal.action.value,
                                "reason": risk_result.reason,
                            },
                            key=f"{self.id}:{signal.ticker}:{risk_result.reason}",
                        )
                return ManagerExecutionResult(
                    manager_id=self.id,
                    parsed_signal_id=parsed_signal_id,
                    allowed=False,
                    rejection_reason=risk_result.reason,
                )

        trade_amount = risk_result.normalized_trade_usd or 0.0
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
                    "portfolio_allocation_pct": signal.portfolio_allocation_pct,
                    "portfolio_value_usd": portfolio_value_usd,
                    "buy_conviction": (
                        signal.buy_conviction.value if signal.buy_conviction else None
                    ),
                    "normalized_trade_usd": trade_amount,
                },
            )
            if (
                self.alert_service
                and not order_result.simulation
                and self.alert_service.on_live_trades
            ):
                await self.alert_service.send(
                    "live_order_submitted",
                    {
                        "manager_id": self.id,
                        "tweet_id": tweet.tweet_id,
                        "ticker": signal.ticker,
                        "action": signal.action.value,
                        "amount_usd": trade_amount,
                        "status": order_result_status,
                    },
                    key=f"trade:{trade.id}",
                )
        else:
            self.logger.info(
                "trade_failed",
                extra={
                    "event_type": "trade_failed",
                    "manager_id": self.id,
                    "tweet_id": tweet.tweet_id,
                    "ticker": signal.ticker,
                    "action": signal.action.value,
                    "error": order_result.error_message,
                },
            )
            if self.alert_service and self.alert_service.on_worker_errors:
                await self.alert_service.send(
                    "trade_failed",
                    {
                        "manager_id": self.id,
                        "ticker": signal.ticker,
                        "action": signal.action.value,
                        "error": order_result.error_message,
                    },
                    key=f"fail:{self.id}:{tweet.tweet_id}",
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

    async def release_queued_signals(self, *, now: datetime | None = None) -> int:
        """Execute deferred BUY/SELL rows once the regular session is open.

        Past-due queues are expired even outside market hours. Returns how many
        queued rows were released or expired.
        """
        if self._paused or not self.config.enabled:
            return 0
        if not self.settings.defer_outside_market_hours:
            return 0

        moment = now or datetime.now(timezone.utc)
        can_release = (
            not self.settings.trading_window_enabled
            or is_within_regular_market_hours(moment)
        )

        with self.session_factory() as db:
            rows = db.execute(
                select(ParsedSignal)
                .options(selectinload(ParsedSignal.tweet))
                .where(
                    ParsedSignal.manager_id == self.id,
                    ParsedSignal.deferred_status == DEFERRED_QUEUED,
                    ParsedSignal.action.in_([SignalAction.BUY, SignalAction.SELL]),
                )
                .order_by(ParsedSignal.created_at.asc())
            ).scalars().all()
            pending = [(row.id, row.deferred_expires_at, row.tweet) for row in rows]

        handled = 0
        for signal_id, expires_at, tweet_row in pending:
            if expires_at is not None:
                expires = expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if moment >= expires:
                    self._expire_queued_signal(signal_id)
                    handled += 1
                    continue

            if not can_release or tweet_row is None:
                continue

            signal = self._trade_signal_from_parsed(signal_id)
            if signal is None:
                continue
            ingested = _ingested_from_tweet(tweet_row)
            await self.evaluate_and_execute(
                signal,
                ingested,
                as_of=moment,
                existing_parsed_signal_id=signal_id,
            )
            handled += 1
        return handled

    def _expire_queued_signal(self, parsed_signal_id: int) -> None:
        with self.session_factory() as db:
            row = db.get(ParsedSignal, parsed_signal_id)
            if row is None or row.deferred_status != DEFERRED_QUEUED:
                return
            row.deferred_status = DEFERRED_EXPIRED
            row.rejection_reason = "deferred_expired"
            db.commit()
        self.logger.info(
            "deferred_signal_expired",
            extra={
                "event_type": "signal_queued",
                "manager_id": self.id,
                "parsed_signal_id": parsed_signal_id,
            },
        )

    def _trade_signal_from_parsed(self, parsed_signal_id: int) -> TradeSignal | None:
        with self.session_factory() as db:
            row = db.get(ParsedSignal, parsed_signal_id)
            if row is None:
                return None
            buy_conviction = None
            if row.action == SignalAction.BUY and row.raw_text:
                buy_conviction = infer_buy_conviction(row.raw_text)
            return TradeSignal(
                source_tweet_id=row.source_tweet_id,
                ticker=row.ticker,
                action=row.action,
                confidence=row.confidence,
                strength=row.strength,
                score=row.score,
                raw_text=row.raw_text,
                suggested_trade_usd=row.suggested_trade_usd,
                portfolio_allocation_pct=row.portfolio_allocation_pct,
                sell_fraction=row.sell_fraction,
                target_portfolio_pct=row.target_portfolio_pct,
                sell_sizing_explicit=bool(row.sell_sizing_explicit),
                buy_conviction=buy_conviction,
                needs_review=bool(row.needs_review),
                review_reason=row.review_reason,
            )

    async def record_watch(
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
        if signal.ticker is None or signal.watch_conviction is None:
            return ManagerExecutionResult(
                manager_id=self.id,
                allowed=False,
                rejection_reason="invalid_watch_signal",
            )

        with self.session_factory() as db:
            parsed_signal = ParsedSignal(
                tweet_pk=tweet.tweet_pk,
                source_tweet_id=signal.source_tweet_id,
                ticker=signal.ticker,
                action=SignalAction.WATCH,
                confidence=signal.confidence,
                strength=signal.strength,
                score=signal.score,
                raw_text=signal.raw_text,
                suggested_trade_usd=0.0,
                rejection_reason=None,
                watch_conviction=signal.watch_conviction.value,
                needs_review=False,
                review_reason=None,
                manager_id=self.id,
            )
            db.add(parsed_signal)
            db.flush()
            parsed_signal_id = parsed_signal.id
            self.risk_manager.watchlist.upsert(
                signal.ticker,
                db,
                manager_id=self.id,
                watch_conviction=signal.watch_conviction,
                source_tweet_id=signal.source_tweet_id,
            )

        self.logger.info(
            "watch_signal_recorded",
            extra={
                "event_type": "watchlist",
                "manager_id": self.id,
                "tweet_id": tweet.tweet_id,
                "ticker": signal.ticker,
                "watch_conviction": signal.watch_conviction.value,
                "confidence": signal.confidence,
            },
        )
        return ManagerExecutionResult(
            manager_id=self.id,
            parsed_signal_id=parsed_signal_id,
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

        portfolio_value = await self._fetch_portfolio_value()
        from app.risk.portfolio_sizing import resolve_max_sell_notional_usd

        max_sell_notional = max(
            self.risk_manager.config.min_sell_notional_usd,
            resolve_max_sell_notional_usd(
                portfolio_value,
                self.risk_manager.config.max_sell_notional_pct,
            ),
        )
        return resolve_sell_order(
            holding,
            sell_fraction,
            max_trade_size_usd=max_sell_notional,
            min_trade_notional_usd=self.risk_manager.config.min_sell_notional_usd,
        )

    async def _fetch_portfolio_value(self) -> float:
        """Return CK sleeve notional for % math (legacy helper name kept for callers)."""
        return self.settings.resolved_ck_portfolio_usd

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


def _ingested_from_tweet(tweet: Tweet) -> IngestedTweet:
    return IngestedTweet(
        tweet_pk=tweet.id,
        tweet_id=tweet.tweet_id,
        account=tweet.account,
        text=tweet.text,
        posted_at=tweet.posted_at,
        fetched_at=tweet.fetched_at,
        is_reply=tweet.is_reply,
        is_retweet=tweet.is_retweet,
    )
