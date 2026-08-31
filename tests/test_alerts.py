from __future__ import annotations

import asyncio

from app.services.alerts import AlertService


def test_alert_cooldown_dedupes(monkeypatch) -> None:
    posts: list[str] = []

    def fake_post(self, text, payload, event_type):
        posts.append(text)
        return True

    monkeypatch.setattr(AlertService, "_post", fake_post)
    service = AlertService(
        webhook_url="https://example.com/hook",
        enabled=True,
        cooldown_seconds=60,
    )

    assert asyncio.run(service.send("live_order_submitted", {"ticker": "NVDA"}, key="NVDA"))
    assert not asyncio.run(service.send("live_order_submitted", {"ticker": "NVDA"}, key="NVDA"))
    assert len(posts) == 1


def test_alert_rejection_filter() -> None:
    service = AlertService(
        webhook_url="https://example.com/hook",
        enabled=True,
        on_rejected_signals=True,
        rejected_reasons=["insufficient_cash", "not_in_portfolio"],
    )
    assert service.should_alert_rejection("insufficient_cash")
    assert service.should_alert_rejection("not_in_portfolio:META")
    assert not service.should_alert_rejection("outside_market_hours")
    service.on_rejected_signals = False
    assert not service.should_alert_rejection("insufficient_cash")
