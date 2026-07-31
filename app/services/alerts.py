from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any


class AlertService:
    """Low-noise webhook alerts with per-key cooldown dedupe."""

    def __init__(
        self,
        *,
        webhook_url: str | None,
        enabled: bool = True,
        on_live_trades: bool = True,
        on_rejected_signals: bool = False,
        rejected_reasons: list[str] | None = None,
        on_worker_errors: bool = True,
        cooldown_seconds: int = 60,
        logger: logging.Logger | None = None,
    ) -> None:
        self.webhook_url = (webhook_url or "").strip() or None
        self.enabled = enabled and self.webhook_url is not None
        self.on_live_trades = on_live_trades
        self.on_rejected_signals = on_rejected_signals
        self.rejected_reasons = [r.strip().lower() for r in (rejected_reasons or []) if r.strip()]
        self.on_worker_errors = on_worker_errors
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.logger = logger or logging.getLogger(__name__)
        self._last_sent: dict[str, float] = {}

    def should_alert_rejection(self, reason: str | None) -> bool:
        if not self.on_rejected_signals or not reason:
            return False
        if not self.rejected_reasons:
            return True
        lowered = reason.lower()
        return any(lowered.startswith(prefix) or prefix in lowered for prefix in self.rejected_reasons)

    async def send(self, event_type: str, payload: dict[str, Any], *, key: str | None = None) -> bool:
        if not self.enabled:
            return False
        dedupe_key = f"{event_type}:{key or ''}"
        now = time.monotonic()
        last = self._last_sent.get(dedupe_key)
        if last is not None and (now - last) < self.cooldown_seconds:
            return False
        text = self._format_message(event_type, payload)
        ok = await asyncio.to_thread(self._post, text, payload, event_type)
        if ok:
            self._last_sent[dedupe_key] = now
        return ok

    def send_sync(self, event_type: str, payload: dict[str, Any], *, key: str | None = None) -> bool:
        """Synchronous send for non-async call sites (e.g. Robinhood session)."""
        if not self.enabled:
            return False
        dedupe_key = f"{event_type}:{key or ''}"
        now = time.monotonic()
        last = self._last_sent.get(dedupe_key)
        if last is not None and (now - last) < self.cooldown_seconds:
            return False
        text = self._format_message(event_type, payload)
        ok = self._post(text, payload, event_type)
        if ok:
            self._last_sent[dedupe_key] = now
        return ok

    def _format_message(self, event_type: str, payload: dict[str, Any]) -> str:
        bits = [f"[{event_type}]"]
        for field in ("ticker", "action", "amount_usd", "status", "reason", "error", "manager_id", "tweet_id"):
            if field in payload and payload[field] is not None:
                bits.append(f"{field}={payload[field]}")
        if "text" in payload and payload["text"]:
            bits.append(str(payload["text"])[:240])
        return " ".join(bits)

    def _post(self, text: str, payload: dict[str, Any], event_type: str) -> bool:
        assert self.webhook_url is not None
        body = json.dumps(
            {
                "text": text,
                "content": text,
                "event_type": event_type,
                "payload": payload,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.logger.warning(
                "alert_webhook_failed",
                extra={"event_type": "alert", "error": str(exc)},
            )
            return False
