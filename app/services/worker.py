from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.execution.broker import Broker
from app.ingestion.service import TweetIngestionService
from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.models.schemas import WorkerStateSnapshot
from app.parsing.signal_parser import RuleBasedSignalParser
from app.risk.risk_manager import RiskManager
from app.services.audit import ExecutionAuditLogger


class BotWorker:
    """Async polling worker that processes tweets into trades."""

    def __init__(
        self,
        settings: Settings,
        ingestion_service: TweetIngestionService,
        parser: RuleBasedSignalParser,
        risk_manager: RiskManager,
        broker: Broker,
        session_factory: Callable[[], Session],
        audit_logger: ExecutionAuditLogger,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.ingestion_service = ingestion_service
        self.parser = parser
        self.risk_manager = risk_manager
        self.broker = broker
        self.session_factory = session_factory
        self.audit_logger = audit_logger
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
        self.audit_logger.write("INFO", "worker", "worker_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.audit_logger.write("INFO", "worker", "worker_stopped")

    def pause(self) -> None:
        self._paused = True
        self.audit_logger.write("INFO", "worker", "worker_paused")

    def resume(self) -> None:
        self._paused = False
        self.audit_logger.write("INFO", "worker", "worker_resumed")

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
            signal = self.parser.parse(tweet.text, source_tweet_id=tweet.tweet_id)

            with self.session_factory() as db:
                    risk_result = self.risk_manager.evaluate(signal, db)
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
                    self.audit_logger.write(
                        "WARNING",
                        "signal_rejected",
                        "signal_rejected",
                        {
                            "tweet_id": tweet.tweet_id,
                            "ticker": signal.ticker,
                            "reason": risk_result.reason,
                        },
                    )
                    continue

            trade_amount = risk_result.normalized_trade_usd or self.settings.default_trade_size_usd
            order_result = await self._execute_order(signal.action, signal.ticker or "", trade_amount)

            with self.session_factory() as db:
                db.add(
                    Trade(
                        parsed_signal_id=parsed_signal_id,
                        ticker=signal.ticker or "",
                        action=signal.action,
                        amount_usd=trade_amount,
                        quantity=order_result.quantity,
                        status=order_result.status,
                        simulation=order_result.simulation,
                        broker_order_id=order_result.order_id,
                        response_json=json.dumps(order_result.raw_response or {}, default=str),
                    )
                )
                db.commit()

            self.audit_logger.write(
                "INFO",
                "trade_executed",
                "trade_executed",
                {
                    "tweet_id": tweet.tweet_id,
                    "ticker": signal.ticker,
                    "action": signal.action.value,
                    "amount_usd": trade_amount,
                    "status": order_result.status,
                    "simulation": order_result.simulation,
                },
            )

    async def _execute_order(self, action: SignalAction, ticker: str, amount_usd: float):
        if action == SignalAction.BUY:
            return await self.broker.buy_market(ticker=ticker, amount_usd=amount_usd)
        if action == SignalAction.SELL:
            return await self.broker.sell_market(ticker=ticker, amount_usd=amount_usd)

        raise ValueError(f"Unsupported signal action for execution: {action}")
