from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.ingestion.clients import PlaywrightTwitterClient, TweetData, _is_browser_gone_error

CLOSED_ERROR = "Page.goto: Target page, context or browser has been closed"


class FakePage:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


class FakeClient(PlaywrightTwitterClient):
    """Replaces the real browser with fake pages and scripted profile loads."""

    def __init__(self, goto_behaviors: list, **kwargs) -> None:
        super().__init__(require_login=False, **kwargs)
        self.goto_behaviors = goto_behaviors
        self.opened_pages: list[FakePage] = []
        self.visited_pages: list[FakePage] = []

    async def _open_page(self) -> FakePage:
        page = FakePage(f"page-{len(self.opened_pages) + 1}")
        self.opened_pages.append(page)
        return page

    async def _ensure_authenticated(self, page) -> None:
        return

    async def _goto_profile(self, page, account: str) -> None:
        self.visited_pages.append(page)
        behavior = self.goto_behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        if behavior == "hang":
            await asyncio.sleep(3600)

    async def _scroll_collect_tweets(self, page, since_utc, max_scrolls, scroll_pause_ms) -> dict[str, TweetData]:
        return {}


def test_is_browser_gone_error() -> None:
    assert _is_browser_gone_error(Exception(CLOSED_ERROR))
    assert _is_browser_gone_error(Exception("Locator.count: Target page, context or browser has been closed"))
    assert _is_browser_gone_error(Exception("Browser.new_page: Target closed"))
    assert not _is_browser_gone_error(Exception("net::ERR_CONNECTION_CLOSED"))
    assert not _is_browser_gone_error(Exception("playwright_timeout_loading_x_profile"))


async def test_closed_browser_reconnects_and_retries_same_poll() -> None:
    client = FakeClient([Exception(CLOSED_ERROR), "ok"])

    tweets = await client.fetch_recent_tweets("CKCapitalxx")

    assert tweets == []
    assert len(client.opened_pages) == 2
    assert client.visited_pages == client.opened_pages


async def test_reconnect_failure_raises_and_next_poll_reconnects() -> None:
    client = FakeClient([Exception(CLOSED_ERROR), Exception(CLOSED_ERROR), "ok"])

    with pytest.raises(Exception, match="has been closed"):
        await client.fetch_recent_tweets("CKCapitalxx")
    assert client._page is None

    await client.fetch_recent_tweets("CKCapitalxx")
    assert len(client.opened_pages) == 3


async def test_closed_page_is_replaced_before_fetch() -> None:
    client = FakeClient(["ok", "ok"])
    await client.fetch_recent_tweets("CKCapitalxx")
    client.opened_pages[0].closed = True

    await client.fetch_recent_tweets("CKCapitalxx")

    assert len(client.opened_pages) == 2
    assert client.visited_pages[-1] is client.opened_pages[1]


async def test_fetch_recent_keeps_the_newest_tweets() -> None:
    client = PlaywrightTwitterClient()
    base = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
    payload = [
        TweetData(
            tweet_id=str(offset),
            text="t",
            posted_at=base + timedelta(minutes=offset),
            is_reply=False,
            is_retweet=False,
        )
        for offset in range(4)
    ]

    async def fake_since(**_kwargs: object) -> list[TweetData]:
        return payload

    client.fetch_tweets_since = fake_since  # type: ignore[method-assign]
    selected = await client.fetch_recent_tweets("CKCapitalxx", limit=2)

    assert [item.tweet_id for item in selected] == ["2", "3"]


async def test_hung_poll_times_out_and_drops_connection() -> None:
    client = FakeClient(["hang", "ok"], poll_timeout_seconds=0.05)

    with pytest.raises(RuntimeError, match="playwright_poll_timeout"):
        await client.fetch_recent_tweets("CKCapitalxx")
    assert client._page is None

    await client.fetch_recent_tweets("CKCapitalxx")
    assert len(client.opened_pages) == 2
