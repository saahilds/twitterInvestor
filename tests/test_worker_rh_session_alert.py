from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import Settings
from app.execution.robinhood_session import ReauthJobSnapshot
from app.services.worker import BotOrchestrator


def _orchestrator(session_mgr, alerts) -> BotOrchestrator:
    return BotOrchestrator(
        settings=Settings(broker_backend="robinhood"),
        ingestion_service=MagicMock(),
        parser=MagicMock(),
        managers=[],
        session_factory=MagicMock(),
        audit_logger=MagicMock(),
        logger=logging.getLogger("test"),
        alert_service=alerts,
    )


@pytest.mark.asyncio
async def test_session_alert_skipped_during_reauth(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.worker.should_alert_session_status",
        lambda _status: True,
    )
    session = MagicMock()
    session.snapshot.return_value = MagicMock(
        logged_in=False,
        last_error="reauth_in_progress",
    )
    session.reauth_snapshot.return_value = ReauthJobSnapshot(
        status="awaiting_approval",
        started_at=None,
        finished_at=None,
        error=None,
        message="waiting",
    )
    alerts = MagicMock()
    alerts.send = AsyncMock(return_value=True)
    orch = _orchestrator(session, alerts)
    monkeypatch.setattr(orch, "_robinhood_session_manager", lambda: session)

    await orch._maybe_alert_robinhood_session()
    alerts.send.assert_not_called()


@pytest.mark.asyncio
async def test_session_alert_fires_when_logged_out(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.worker.should_alert_session_status",
        lambda _status: True,
    )
    monkeypatch.setattr("app.services.worker.mark_session_alert_sent", lambda _status: None)
    session = MagicMock()
    session.snapshot.return_value = MagicMock(
        logged_in=False,
        last_error="robinhood_login_required",
    )
    session.reauth_snapshot.return_value = ReauthJobSnapshot(
        status="idle",
        started_at=None,
        finished_at=None,
        error=None,
        message=None,
    )
    alerts = MagicMock()
    alerts.send = AsyncMock(return_value=True)
    orch = _orchestrator(session, alerts)
    monkeypatch.setattr(orch, "_robinhood_session_manager", lambda: session)

    await orch._maybe_alert_robinhood_session()
    alerts.send.assert_awaited_once()
    assert alerts.send.await_args.args[0] == "robinhood_logged_out"
