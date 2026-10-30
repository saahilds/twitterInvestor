from __future__ import annotations

import logging

from app.ingestion.clients import TweetData
from app.ingestion.service import TweetIngestionService
from app.services import alerts as alerts_module
from app.services.alerts import AlertService, is_ntfy_url
from app.services.heartbeat import HeartbeatPinger


class FlakyTwitterClient:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        if not self.outcomes.pop(0):
            raise RuntimeError("Page.goto: Target page, context or browser has been closed")
        return []


class RecordingAlerts:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def send(self, event_type: str, payload: dict, *, key: str | None = None) -> bool:
        self.events.append(event_type)
        return True


def _service(session_factory, outcomes: list[bool]) -> tuple[TweetIngestionService, RecordingAlerts]:
    service = TweetIngestionService(
        twitter_client=FlakyTwitterClient(outcomes),
        session_factory=session_factory,
        target_account="CKCapitalxx",
        fetch_limit=20,
        ignore_replies=True,
        ignore_retweets=True,
        logger=logging.getLogger("test"),
        feed_down_after_failures=3,
    )
    alerts = RecordingAlerts()
    service.alert_service = alerts
    return service, alerts


async def test_feed_down_alerts_once_then_recovers(session_factory) -> None:
    service, alerts = _service(session_factory, [False] * 5 + [True])

    for _ in range(5):
        await service.poll()
    assert alerts.events == ["x_feed_down"]
    assert service.consecutive_failures == 5
    assert service.last_success_at is None

    await service.poll()
    assert alerts.events == ["x_feed_down", "x_feed_recovered"]
    assert service.consecutive_failures == 0
    assert service.last_success_at is not None


async def test_short_blips_do_not_alert(session_factory) -> None:
    service, alerts = _service(session_factory, [False, False, True, False, True])

    for _ in range(5):
        await service.poll()
    assert alerts.events == []


def test_ntfy_alerts_post_plain_text_with_headers(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        captured["body"] = request.data.decode()
        captured["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr(alerts_module.urllib.request, "urlopen", fake_urlopen)
    service = AlertService(webhook_url="https://ntfy.sh/ckbot-test", enabled=True)

    assert service.send_sync("x_feed_down", {"error": "boom"})
    assert captured["body"] == "[x_feed_down] error=boom"
    assert captured["headers"]["Title"] == "Trading bot: x_feed_down"
    assert captured["headers"]["Priority"] == "5"


def test_is_ntfy_url() -> None:
    assert is_ntfy_url("https://ntfy.sh/topic")
    assert not is_ntfy_url("https://discord.com/api/webhooks/1/abc")


async def test_heartbeat_is_rate_limited(monkeypatch) -> None:
    pings: list[str] = []
    pinger = HeartbeatPinger(url="https://hc-ping.com/abc", interval_seconds=120)
    monkeypatch.setattr(pinger, "_ping", lambda: pings.append("ping") or True)

    assert await pinger.beat()
    assert not await pinger.beat()
    assert pings == ["ping"]


async def test_heartbeat_disabled_without_url() -> None:
    assert not await HeartbeatPinger(url=None).beat()


def test_restart_refused_when_not_running_as_service(monkeypatch) -> None:
    from app.api import routes

    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert routes._running_under_systemd() is False
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    assert routes._running_under_systemd() is True
