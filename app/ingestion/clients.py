from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import time
from typing import Protocol

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import BrowserContext, Page, Playwright
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - runtime environment dependent
    PlaywrightTimeoutError = None
    BrowserContext = None  # type: ignore[assignment]
    Page = None  # type: ignore[assignment]
    Playwright = None  # type: ignore[assignment]
    async_playwright = None


@dataclass(slots=True)
class TweetData:
    tweet_id: str
    text: str
    posted_at: datetime
    is_reply: bool
    is_retweet: bool
    url: str | None = None


class TwitterClient(Protocol):
    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        """Fetch most recent tweets for a single account."""


class PlaywrightTwitterClient:
    """Playwright-backed X profile reader using persistent browser profile."""

    status_href_pattern = re.compile(r"/status/(\d+)")

    def __init__(
        self,
        timeout_ms: int = 20_000,
        headless: bool = False,
        user_data_dir: str = ".playwright/x-profile",
        channel: str = "chrome",
        require_login: bool = True,
        login_timeout_seconds: int = 300,
        logger: logging.Logger | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.channel = channel
        self.require_login = require_login
        self.login_timeout_seconds = login_timeout_seconds
        self.logger = logger or logging.getLogger("trading_bot")

        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._login_checked = False
        self._lock = asyncio.Lock()

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        if async_playwright is None:
            raise RuntimeError("playwright is unavailable in this environment")

        async with self._lock:
            page = await self._ensure_page()
            await self._ensure_authenticated(page)
            rows = await self._fetch_profile_rows(page, account)

        tweets: list[TweetData] = []
        seen_ids: set[str] = set()
        for row in rows:
            href = row.get("href") if isinstance(row, dict) else None
            tweet_id = _extract_status_id(href or "")
            if not tweet_id or tweet_id in seen_ids:
                continue

            seen_ids.add(tweet_id)
            posted_at = _parse_x_datetime(row.get("datetime"))
            text = (row.get("text") or "").strip()
            absolute_url = href if isinstance(href, str) and href.startswith("http") else f"https://x.com{href}"
            tweets.append(
                TweetData(
                    tweet_id=tweet_id,
                    text=text,
                    posted_at=posted_at,
                    is_reply=bool(row.get("isReply")),
                    is_retweet=bool(row.get("isRetweet")),
                    url=absolute_url,
                )
            )
            if len(tweets) >= limit:
                break
        return tweets

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
                self._page = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    async def _ensure_page(self) -> Page:
        if self._page is not None:
            return self._page

        self._playwright = await async_playwright().start()
        profile_dir = str(Path(self.user_data_dir).expanduser().resolve())
        browser_channel = self.channel.strip() or None

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel=browser_channel,
            headless=self.headless,
            viewport={"width": 1440, "height": 900},
        )

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self.logger.info(
            "playwright_persistent_context_ready",
            extra={
                "event_type": "playwright_context",
                "profile_dir": profile_dir,
                "headless": self.headless,
                "channel": browser_channel or "default",
            },
        )
        return self._page

    async def _ensure_authenticated(self, page: Page) -> None:
        if self._login_checked or not self.require_login:
            return

        await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=self.timeout_ms)
        login_required = await _is_login_required(page)
        if not login_required:
            self._login_checked = True
            self.logger.info(
                "x_session_already_authenticated",
                extra={"event_type": "x_auth"},
            )
            return

        if self.headless:
            raise RuntimeError("x_login_required_but_browser_is_headless")

        self.logger.warning(
            "x_login_required_manual_action",
            extra={
                "event_type": "x_auth",
                "message_hint": "Complete login in opened Chrome window. Session will persist.",
            },
        )
        await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=self.timeout_ms)
        deadline = time.monotonic() + max(30, self.login_timeout_seconds)
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            if not await _is_login_required(page):
                self._login_checked = True
                self.logger.info(
                    "x_login_completed_and_persisted",
                    extra={"event_type": "x_auth"},
                )
                return
        raise RuntimeError("x_login_timeout_waiting_for_manual_auth")

    async def _fetch_profile_rows(self, page: Page, account: str) -> list[dict]:
        url = f"https://x.com/{account}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            await page.wait_for_selector("article", timeout=self.timeout_ms)
            rows = await page.eval_on_selector_all(
                "article",
                """(articles) => {
                  return articles.map((article) => {
                    const linkNode = article.querySelector('a[href*="/status/"]');
                    const timeNode = article.querySelector('time');
                    const tweetTextNode = article.querySelector('[data-testid="tweetText"]');
                    const text = tweetTextNode ? tweetTextNode.innerText : article.innerText;
                    return {
                      href: linkNode ? linkNode.getAttribute('href') : null,
                      datetime: timeNode ? timeNode.getAttribute('datetime') : null,
                      text: text || '',
                      isReply: article.innerText.includes('Replying to'),
                      isRetweet: article.innerText.includes('Reposted'),
                    };
                  });
                }""",
            )
            if isinstance(rows, list):
                return rows
            return []
        except Exception as exc:
            if PlaywrightTimeoutError is not None and isinstance(exc, PlaywrightTimeoutError):
                raise RuntimeError("playwright_timeout_loading_x_profile") from exc
            raise


class MockTwitterClient:
    """In-memory fake Twitter client for deterministic tests."""

    def __init__(self) -> None:
        self._tweets: list[TweetData] = []

    def add_tweet(self, tweet: TweetData) -> None:
        self._tweets.insert(0, tweet)

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        return self._tweets[:limit]


def _parse_x_datetime(raw_datetime: object) -> datetime:
    if isinstance(raw_datetime, str) and raw_datetime:
        normalized = raw_datetime.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _extract_status_id(href: str) -> str | None:
    match = PlaywrightTwitterClient.status_href_pattern.search(href)
    if match:
        return match.group(1)
    return None


async def _is_login_required(page: Page) -> bool:
    url = page.url.lower()
    if "/i/flow/login" in url:
        return True

    if await page.locator('input[name="text"]').count() > 0:
        return True
    if await page.locator('input[name="password"]').count() > 0:
        return True
    if await page.locator('a[href="/i/flow/login"]').count() > 0:
        return True

    return False
