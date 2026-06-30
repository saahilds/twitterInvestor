from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.ingestion.service import TweetIngestionService
from app.models.db_models import SignalAction
from app.models.schemas import ManagerStateSnapshot, OrchestratorStateSnapshot
from app.parsing.factory import SignalParser
from app.services.account_manager import AccountManager
from app.services.audit import ExecutionAuditLogger


class BotOrchestrator:
    """Polls tweets once and fans out execution to account managers."""

    def __init__(
        self,
        settings: Settings,
        ingestion_service: TweetIngestionService,
        parser: SignalParser,
        managers: list[AccountManager],
        session_factory: Callable[[], Session],
        audit_logger: ExecutionAuditLogger,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.ingestion_service = ingestion_service
        self.parser = parser
        self.managers = managers
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
        self._task = asyncio.create_task(self._run_loop(), name="bot-orchestrator")
        self.logger.debug("orchestrator_started", extra={"event_type": "worker"})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.debug("orchestrator_stopped", extra={"event_type": "worker"})

    def pause(self, manager_id: str | None = None) -> None:
        if manager_id is None:
            self._paused = True
            for manager in self.managers:
                manager.pause()
            self.logger.debug("orchestrator_paused_all", extra={"event_type": "worker"})
            return
        manager = self.get_manager(manager_id)
        if manager is not None:
            manager.pause()
            self.logger.debug("manager_paused", extra={"event_type": "worker", "manager_id": manager_id})

    def resume(self, manager_id: str | None = None) -> None:
        if manager_id is None:
            self._paused = False
            for manager in self.managers:
                manager.resume()
            self.logger.debug("orchestrator_resumed_all", extra={"event_type": "worker"})
            return
        manager = self.get_manager(manager_id)
        if manager is not None:
            manager.resume()
            self.logger.debug("manager_resumed", extra={"event_type": "worker", "manager_id": manager_id})

    def get_manager(self, manager_id: str) -> AccountManager | None:
        for manager in self.managers:
            if manager.id == manager_id:
                return manager
        return None

    def snapshot(self) -> OrchestratorStateSnapshot:
        any_paused = self._paused or any(manager.paused for manager in self.managers)
        return OrchestratorStateSnapshot(
            running=self._running,
            paused=any_paused,
            iteration_count=self._iteration_count,
            last_error=self._last_error,
            managers=[
                ManagerStateSnapshot(
                    manager_id=manager.id,
                    account_number=manager.account_number,
                    paused=manager.paused or self._paused,
                    enabled=manager.config.enabled,
                )
                for manager in self.managers
            ],
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
                self.logger.exception("orchestrator_iteration_failed", extra={"error": str(exc)})
                self.audit_logger.write(
                    "ERROR",
                    "worker_error",
                    "orchestrator_iteration_failed",
                    {"error": str(exc)},
                )

            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _process_iteration(self) -> None:
        new_tweets = await self.ingestion_service.poll()
        if not new_tweets:
            return

        manager_ids = [manager.id for manager in self.managers if manager.config.enabled]
        with self.session_factory() as db:
            registry = self.managers[0].risk_manager.registry if self.managers else None
            watchlist = self.managers[0].risk_manager.watchlist if self.managers else None
            known_tickers: set[str] = set()
            if registry is not None:
                known_tickers |= registry.union_tickers(db, manager_ids)
            if watchlist is not None:
                known_tickers |= watchlist.union_tickers(db, manager_ids)
            if watchlist is not None and manager_ids:
                pruned = watchlist.prune_stale(db, manager_ids)
                if pruned:
                    self.logger.info(
                        "watchlist_pruned_stale",
                        extra={"event_type": "watchlist", "removed": pruned},
                    )

        for tweet in new_tweets:
            signal = self.parser.parse(
                tweet.text,
                source_tweet_id=tweet.tweet_id,
                extra_known_tickers=known_tickers,
            )
            if signal.action == SignalAction.IGNORE:
                continue

            if signal.action == SignalAction.WATCH:
                for manager in self.managers:
                    if not manager.config.enabled:
                        continue
                    await manager.record_watch(signal, tweet)
                continue

            for manager in self.managers:
                if not manager.config.enabled:
                    continue
                await manager.evaluate_and_execute(signal, tweet)


# Backward-compatible alias used in older tests/imports.
BotWorker = BotOrchestrator
