from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.execution.robinhood_auth_age import (
    compute_auth_age,
    mark_auth_alert_sent,
    should_alert_auth_status,
)
from app.execution.robinhood_broker import RobinhoodBroker
from app.ingestion.backfill_lock import backfill_in_progress
from app.ingestion.host_chrome import quit_bot_chrome, start_bot_chrome
from app.ingestion.service import TweetIngestionService
from app.models.db_models import SignalAction
from app.models.schemas import ManagerStateSnapshot, OrchestratorStateSnapshot
from app.parsing.factory import SignalParser
from app.risk.market_hours import (
    DIGEST_CHECKPOINTS,
    digest_date_et,
    idle_sleep_seconds,
    should_poll_x_feed,
    to_eastern,
)
from app.services.account_manager import AccountManager
from app.services.alerts import AlertService
from app.services.audit import ExecutionAuditLogger
from app.services.daily_digest import DailyDigestService
from app.services.heartbeat import HeartbeatPinger
from app.services.portfolio_snapshot import maybe_record_snapshot


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
        alert_service: AlertService | None = None,
        digest_service: DailyDigestService | None = None,
        heartbeat: HeartbeatPinger | None = None,
    ) -> None:
        self.settings = settings
        self.ingestion_service = ingestion_service
        self.parser = parser
        self.managers = managers
        self.session_factory = session_factory
        self.audit_logger = audit_logger
        self.logger = logger
        self.alert_service = alert_service
        self.digest_service = digest_service or DailyDigestService(session_factory)
        self.heartbeat = heartbeat

        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._paused = False
        self._iteration_count = 0
        self._last_error: str | None = None
        self._last_snapshot_at = 0.0
        self._last_digest_rebuild_at = 0.0
        self._feed_idle: bool | None = None
        self._host_chrome_stopped = False

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

    def _should_poll_feed(self) -> bool:
        return should_poll_x_feed(
            trading_window_enabled=self.settings.trading_window_enabled,
            poll_only_during_market_hours=self.settings.poll_only_during_market_hours,
        )

    async def _run_loop(self) -> None:
        while self._running:
            if self._paused or not self._should_poll_feed():
                await self._idle_once()
                continue

            if self._feed_idle is not False:
                self._host_chrome_stopped = False
                await self._start_host_chrome()
                self.logger.info(
                    "x_feed_active",
                    extra={"event_type": "x_profile", "reason": "market_hours"},
                )
                self._feed_idle = False

            try:
                had_activity = await self._process_iteration()
                self._iteration_count += 1
                self._last_error = None
                await self._run_maintenance(had_activity=had_activity)
                # Only report healthy when tweets are actually being fetched.
                # A parked overnight feed still heartbeats from _idle_once.
                if getattr(self.ingestion_service, "consecutive_failures", 0) == 0:
                    await self._beat()
            except Exception as exc:
                self._last_error = str(exc)
                self.logger.exception("orchestrator_iteration_failed", extra={"error": str(exc)})
                self.audit_logger.write(
                    "ERROR",
                    "worker_error",
                    "orchestrator_iteration_failed",
                    {"error": str(exc)},
                )
                if self.alert_service and self.alert_service.on_worker_errors:
                    await self.alert_service.send(
                        "worker_error",
                        {"error": str(exc)},
                        key="orchestrator_iteration_failed",
                    )

            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _idle_once(self) -> None:
        """Stay up for heartbeats and the digest, without loading X."""
        reason = "paused" if self._paused else "outside_market_hours"
        if self._feed_idle is not True:
            self.logger.info("x_feed_idle", extra={"event_type": "x_profile", "reason": reason})
            self._feed_idle = True
        if backfill_in_progress(self.settings):
            # The 7 AM backfill starts Chrome under the lock. Quit again once it drops.
            self._host_chrome_stopped = False
        elif self._paused and self._should_poll_feed():
            await self._park_feed()
        elif not self._host_chrome_stopped:
            self._host_chrome_stopped = await self._quit_host_chrome()
        await self._run_maintenance(had_activity=False)
        await self._beat()
        await asyncio.sleep(
            idle_sleep_seconds(
                poll_interval_seconds=self.settings.poll_interval_seconds,
                feed_should_poll=False,
            )
        )

    async def _start_host_chrome(self) -> None:
        if not (self.settings.playwright_cdp_url or "").strip():
            return
        try:
            await asyncio.to_thread(start_bot_chrome, self.settings)
        except Exception as exc:
            self.logger.warning(
                "host_chrome_start_failed",
                extra={"event_type": "x_profile", "error": str(exc)},
            )

    async def _quit_host_chrome(self) -> bool:
        """Quit Windows Chrome after the session. Returns True when the process is gone."""
        if not (self.settings.playwright_cdp_url or "").strip():
            return False
        shutdown = getattr(self.ingestion_service.twitter_client, "shutdown_attached_browser", None)
        if shutdown is not None:
            try:
                await shutdown()
            except Exception as exc:
                self.logger.warning(
                    "host_chrome_disconnect_failed",
                    extra={"event_type": "x_profile", "error": str(exc)},
                )
        try:
            stopped = await asyncio.to_thread(quit_bot_chrome, self.settings)
        except Exception as exc:
            self.logger.warning(
                "host_chrome_quit_failed",
                extra={"event_type": "x_profile", "error": str(exc)},
            )
            return False
        if stopped:
            self.logger.info("host_chrome_stopped", extra={"event_type": "x_profile"})
        return stopped

    async def _park_feed(self) -> None:
        park = getattr(self.ingestion_service.twitter_client, "park", None)
        if park is None:
            return
        try:
            await park()
        except Exception as exc:
            self.logger.warning(
                "x_feed_park_failed",
                extra={"event_type": "x_profile", "error": str(exc)},
            )

    async def _beat(self) -> None:
        if self.heartbeat is None:
            return
        try:
            await self.heartbeat.beat()
        except Exception as exc:
            self.logger.warning(
                "heartbeat_failed",
                extra={"event_type": "heartbeat", "error": str(exc)},
            )

    async def _process_iteration(self) -> bool:
        new_tweets = await self.ingestion_service.poll()
        if not new_tweets:
            return False

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
            signals = self.parser.parse(
                tweet.text,
                source_tweet_id=tweet.tweet_id,
                extra_known_tickers=known_tickers,
            )
            for signal in signals:
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
        return True

    async def _run_maintenance(self, *, had_activity: bool) -> None:
        now_mono = time.monotonic()

        await self._maybe_alert_robinhood_auth_age()
        await self._release_deferred_orders()

        if self.settings.snapshot_enabled:
            interval = max(60, self.settings.snapshot_interval_seconds)
            if now_mono - self._last_snapshot_at >= interval:
                for manager in self.managers:
                    if not manager.config.enabled:
                        continue
                    if not isinstance(manager.broker, RobinhoodBroker):
                        continue
                    try:
                        await asyncio.to_thread(
                            maybe_record_snapshot,
                            broker=manager.broker,
                            settings=self.settings,
                            session_factory=self.session_factory,
                            logger=self.logger,
                        )
                    except Exception as exc:
                        self.logger.warning(
                            "snapshot_maintenance_failed",
                            extra={"event_type": "snapshot", "error": str(exc)},
                        )
                self._last_snapshot_at = now_mono

        if not self.settings.daily_digest_enabled:
            return

        rebuild_interval = max(60, self.settings.daily_digest_rebuild_interval_seconds)
        should_rebuild = had_activity and self.settings.daily_digest_live_update
        if now_mono - self._last_digest_rebuild_at >= rebuild_interval:
            should_rebuild = True
        if should_rebuild:
            try:
                await asyncio.to_thread(self.digest_service.rebuild)
                self._last_digest_rebuild_at = now_mono
            except Exception as exc:
                self.logger.warning(
                    "digest_rebuild_failed",
                    extra={"event_type": "digest", "error": str(exc)},
                )

        await self._maybe_digest_checkpoints()

    async def _release_deferred_orders(self) -> None:
        if not self.settings.defer_outside_market_hours:
            return
        for manager in self.managers:
            if not manager.config.enabled:
                continue
            try:
                released = await manager.release_queued_signals()
            except Exception as exc:
                self.logger.warning(
                    "deferred_order_release_failed",
                    extra={
                        "event_type": "signal_queued",
                        "manager_id": manager.id,
                        "error": str(exc),
                    },
                )
                continue
            if released:
                self.logger.info(
                    "deferred_orders_processed",
                    extra={
                        "event_type": "signal_queued",
                        "manager_id": manager.id,
                        "handled": released,
                    },
                )

    async def _maybe_alert_robinhood_auth_age(self) -> None:
        if self.settings.broker_backend != "robinhood":
            return
        if not self.alert_service:
            return
        try:
            age = compute_auth_age(
                max_age_days=self.settings.robinhood_pickle_max_age_days,
                warn_days=self.settings.robinhood_pickle_warn_days,
            )
            if not should_alert_auth_status(age.status):
                return
            days = age.days_remaining
            text = (
                f"Robinhood auth {age.status}: "
                f"{f'{days:.1f}d remaining' if days is not None else 'age unknown'}. "
                "Use dashboard Refresh RH auth or scripts/rh_reauth.sh."
            )
            await self.alert_service.send(
                "robinhood_auth_age",
                {
                    "status": age.status,
                    "days_remaining": age.days_remaining,
                    "age_days": age.age_days,
                    "text": text,
                },
                key=f"robinhood_auth_age:{age.status}",
            )
            mark_auth_alert_sent(age.status)
        except Exception as exc:
            self.logger.warning(
                "robinhood_auth_age_alert_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )

    async def _maybe_digest_checkpoints(self) -> None:
        if not self.settings.daily_digest_enabled:
            return
        from datetime import datetime, timezone

        et = to_eastern(datetime.now(timezone.utc))
        date_key = digest_date_et(et)
        current = et.time()
        for boundary, name in DIGEST_CHECKPOINTS:
            if current < boundary:
                continue
            if self.digest_service.checkpoint_already_sent(date_key, name):
                continue
            if name == "finalize":
                row = await asyncio.to_thread(self.digest_service.finalize, date_key)
                if row is not None and self.alert_service and self.settings.daily_digest_send_webhook:
                    payload = self.digest_service.to_api_dict(row)
                    await self.alert_service.send(
                        "digest_complete",
                        {"text": payload.get("summary_markdown", "")[:3500], "digest_date": date_key},
                        key=f"digest_complete:{date_key}",
                    )
                self.digest_service.mark_checkpoint_sent(date_key, name)
                continue

            row = await asyncio.to_thread(self.digest_service.rebuild, date_key)
            if (
                row is not None
                and self.alert_service
                and self.settings.daily_digest_webhook_on_checkpoints
            ):
                payload = self.digest_service.to_api_dict(row)
                await self.alert_service.send(
                    "digest_checkpoint",
                    {
                        "checkpoint": name,
                        "digest_date": date_key,
                        "text": payload.get("summary_markdown", "")[:3500],
                    },
                    key=f"digest:{date_key}:{name}",
                )
            self.digest_service.mark_checkpoint_sent(date_key, name)


# Backward-compatible alias used in older tests/imports.
BotWorker = BotOrchestrator
