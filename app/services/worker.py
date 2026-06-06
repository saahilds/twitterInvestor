from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.execution.broker import Broker
from app.execution.robinhood_broker import RobinhoodBroker
from app.ingestion.service import TweetIngestionService
from app.models.db_models import ParsedSignal, SignalAction
from app.models.schemas import WorkerStateSnapshot
from app.parsing.factory import SignalParser
from app.risk.risk_manager import RiskManager
from app.services.audit import ExecutionAuditLogger
from app.services.trade_recorder import create_trade_record
from app.services.trade_status import TradeStatusSync, trade_is_terminal


class BotWorker:
    """Async polling worker that processes tweets into trades."""

    def __init__(
        self,
        settings: Settings,
        ingestion_service: TweetIngestionService,
        parser: SignalParser,
        risk_manager: RiskManager,
        broker: Broker,
        session_factory: Callable[[], Session],
        audit_logger: ExecutionAuditLogger,
        logger: logging.Logger,
        trade_status_sync: TradeStatusSync | None = None,
    ) -> None:
        self.settings = settings
        self.ingestion_service = ingestion_service
        self.parser = parser
        self.risk_manager = risk_manager
        self.broker = broker
        self.session_factory = session_factory
        self.audit_logger = audit_logger
        self.trade_status_sync = trade_status_sync
        self.logger = logger

        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._paused = False
        self._iteration_count = 0
        self._last_error: str | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="bot-worker")
        self.logger.debug("worker_started", extra={"event_type": "worker"})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.debug("worker_stopped", extra={"event_type": "worker"})

    def pause(self) -> None:
        self._paused = True
        self.logger.debug("worker_paused", extra={"event_type": "worker"})

    def resume(self) -> None:
        self._paused = False
        self.logger.debug("worker_resumed", extra={"event_type": "worker"})

    def snapshot(self) -> WorkerStateSnapshot:
        return WorkerStateSnapshot(
            running=self._running,
            paused=self._paused,
            iteration_count=self._iteration_count,
            last_error=self._last_error,
        )

    async def _run_loop(self) -> None:
        while self._running:
            if self._paused:
                await asyncio.sleep(1)
                continue

            try:
                await self._process_iteration()
                self._iteration_count += 1
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                self.logger.exception("worker_iteration_failed", extra={"error": str(exc)})
                self.audit_logger.write(
                    "ERROR",
                    "worker_error",
                    "worker_iteration_failed",
                    {"error": str(exc)},
                )

            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _process_iteration(self) -> None:
        new_tweets = await self.ingestion_service.poll()
        if not new_tweets:
            return

        for tweet in new_tweets:
            with self.session_factory() as db:
                known_tickers = self.risk_manager.registry.all_tickers(db)

            signal = self.parser.parse(
                tweet.text,
                source_tweet_id=tweet.tweet_id,
                extra_known_tickers=known_tickers,
            )

            cash_available_usd = None
            if signal.action == SignalAction.BUY and isinstance(self.broker, RobinhoodBroker):
                cash_available_usd = await asyncio.to_thread(self.broker.get_cash_available_usd)

            with self.session_factory() as db:
                risk_result = self.risk_manager.evaluate(
                    signal,
                    db,
                    cash_available_usd=cash_available_usd,
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
                )
                db.add(parsed_signal)
                db.flush()
                parsed_signal_id = parsed_signal.id
                db.commit()

                if not risk_result.allowed:
                    continue

            trade_amount = risk_result.normalized_trade_usd or self.settings.default_trade_size_usd
            order_result = await self._execute_order(signal.action, signal.ticker or "", trade_amount)

            with self.session_factory() as db:
                trade = create_trade_record(
                    parsed_signal_id=parsed_signal_id,
                    source_tweet_id=signal.source_tweet_id,
                    ticker=signal.ticker or "",
                    action=signal.action,
                    amount_usd=trade_amount,
                    order_result=order_result,
                    order_execution_mode=self.settings.order_execution_mode,
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
                    },
                )

            if order_result.status != "failed" and signal.ticker:
                with self.session_factory() as db:
                    self.risk_manager.registry.register(
                        signal.ticker,
                        db,
                        source_tweet_id=signal.source_tweet_id,
                    )

    async def _execute_order(self, action: SignalAction, ticker: str, amount_usd: float):
        if action == SignalAction.BUY:
            if self.settings.order_execution_mode == "limit_at_ask":
                return await self.broker.buy_limit_at_ask(ticker=ticker, amount_usd=amount_usd)
            return await self.broker.buy_market(ticker=ticker, amount_usd=amount_usd)
        if action == SignalAction.SELL:
            if self.settings.live_trading_enabled:
                raise ValueError("live_sell_blocked")
            return await self.broker.sell_market(ticker=ticker, amount_usd=amount_usd)

        raise ValueError(f"Unsupported signal action for execution: {action}")
