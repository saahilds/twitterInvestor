from __future__ import annotations

import asyncio
import logging
import time
import urllib.error
import urllib.request


class HeartbeatPinger:
    """Pings an external dead man's switch (e.g. healthchecks.io) while the bot is healthy."""

    def __init__(
        self,
        *,
        url: str | None,
        interval_seconds: int = 120,
        logger: logging.Logger | None = None,
    ) -> None:
        self.url = (url or "").strip() or None
        self.interval_seconds = max(30, interval_seconds)
        self.logger = logger or logging.getLogger(__name__)
        self._last_ping_at: float | None = None

    async def beat(self) -> bool:
        if self.url is None:
            return False
        now = time.monotonic()
        if self._last_ping_at is not None and now - self._last_ping_at < self.interval_seconds:
            return False
        ok = await asyncio.to_thread(self._ping)
        if ok:
            self._last_ping_at = now
        return ok

    def _ping(self) -> bool:
        assert self.url is not None
        try:
            with urllib.request.urlopen(self.url, timeout=10) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.logger.warning(
                "heartbeat_ping_failed",
                extra={"event_type": "heartbeat", "error": str(exc)},
            )
            return False
