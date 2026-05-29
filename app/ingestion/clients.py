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


_EXTRACT_ARTICLE_ROWS_JS = """(articles) => {
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
}"""


class TwitterClient(Protocol):
    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        """Fetch most recent tweets for a single account."""

    async def fetch_tweets_since(
        self,
        account: str,
        since: datetime,
        max_scrolls: int = 150,
        scroll_pause_ms: int = 1500,
    ) -> list[TweetData]:
        """Fetch tweets posted on or after ``since`` by scrolling the profile timeline."""


class PlaywrightTwitterClient:
    """Playwright-backed X profile reader using persistent browser profile."""

    status_href_pattern = re.compile(r"/status/(\d+)")

    def __init__(
        self,
        timeout_ms: int = 20_000,
        headless: bool = False,
        user_data_dir: str = ".playwright/x-profile",
        channel: str = "chrome",
        cdp_url: str | None = None,
        require_login: bool = True,
        login_timeout_seconds: int = 300,
        backfill_max_scrolls: int = 150,
        backfill_scroll_pause_ms: int = 1500,
        logger: logging.Logger | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.channel = channel
        self.cdp_url = cdp_url.strip() if cdp_url else None
        self.require_login = require_login
        self.login_timeout_seconds = login_timeout_seconds
        self.backfill_max_scrolls = backfill_max_scrolls
        self.backfill_scroll_pause_ms = backfill_scroll_pause_ms
        self.logger = logger or logging.getLogger("trading_bot")

        self._playwright: Playwright | None = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._attached_via_cdp = False
        self._login_checked = False
        self._lock = asyncio.Lock()

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        tweets = await self.fetch_tweets_since(
            account=account,
            since=datetime(1970, 1, 1, tzinfo=timezone.utc),
            max_scrolls=0,
            scroll_pause_ms=0,
        )
        return tweets[:limit]

    async def fetch_tweets_since(
        self,
        account: str,
        since: datetime,
        max_scrolls: int | None = None,
        scroll_pause_ms: int | None = None,
    ) -> list[TweetData]:
        if async_playwright is None:
            raise RuntimeError("playwright is unavailable in this environment")

        since_utc = _as_utc(since)
        scroll_limit = self.backfill_max_scrolls if max_scrolls is None else max_scrolls
        pause_ms = self.backfill_scroll_pause_ms if scroll_pause_ms is None else scroll_pause_ms

        async with self._lock:
            page = await self._ensure_page()
            await self._ensure_authenticated(page)
            await self._goto_profile(page, account)
            collected = await self._scroll_collect_tweets(
                page=page,
                since_utc=since_utc,
                max_scrolls=scroll_limit,
                scroll_pause_ms=pause_ms,
            )

        return sorted(
            [tweet for tweet in collected.values() if tweet.posted_at >= since_utc],
            key=lambda item: item.posted_at,
        )

    async def fetch_tweets_between(
        self,
        account: str,
        start_at: datetime,
        end_at: datetime,
        max_tweets: int = 10_000,
        max_scrolls: int = 2_000,
        scroll_pause_ms: int = 1_000,
    ) -> list[TweetData]:
        """Backfill tweets within a datetime range by scrolling the timeline."""
        if async_playwright is None:
            raise RuntimeError("playwright is unavailable in this environment")

        if max_tweets <= 0:
            return []

        start_at = _as_utc(start_at)
        end_at = _as_utc(end_at)
        if start_at > end_at:
            raise ValueError("start_at must be <= end_at")

        async with self._lock:
            page = await self._ensure_page()
            await self._ensure_authenticated(page)
            await self._goto_profile(page, account)

            collected: dict[str, TweetData] = {}
            stagnant_cycles = 0

            for _ in range(max_scrolls):
                rows = await self._extract_profile_rows(page)
                before_count = len(collected)
                saw_older_than_start = False

                for tweet in _rows_to_tweets(rows):
                    posted_at_utc = _as_utc(tweet.posted_at)
                    tweet.posted_at = posted_at_utc
                    if posted_at_utc < start_at:
                        saw_older_than_start = True
                    if posted_at_utc < start_at or posted_at_utc > end_at:
                        continue
                    if tweet.tweet_id not in collected:
                        collected[tweet.tweet_id] = tweet

                added = len(collected) - before_count
                if added == 0:
                    stagnant_cycles += 1
                else:
                    stagnant_cycles = 0

                if len(collected) >= max_tweets:
                    break
                if saw_older_than_start and stagnant_cycles >= 2:
                    break
                if stagnant_cycles >= 8:
                    break

                previous_height = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(scroll_pause_ms)
                current_height = await page.evaluate("document.body.scrollHeight")
                if current_height <= previous_height and added == 0:
                    stagnant_cycles += 1

            self.logger.info(
                "playwright_backfill_window_complete",
                extra={
                    "event_type": "backfill",
                    "account": account,
                    "count": len(collected),
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                },
            )
            return sorted(collected.values(), key=lambda tweet: tweet.posted_at)

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None and not self._attached_via_cdp:
                await self._context.close()
                self._context = None
                self._page = None
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    # Connected CDP browser may be managed externally.
                    pass
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    async def _ensure_page(self) -> Page:
        if self._page is not None:
            return self._page

        self._playwright = await async_playwright().start()
        if self.cdp_url:
            self._attached_via_cdp = True
            self._browser = await self._playwright.chromium.connect_over_cdp(self.cdp_url)
            contexts = self._browser.contexts
            self._context = contexts[0] if contexts else await self._browser.new_context()
            self.logger.info(
                "playwright_attached_existing_chrome",
                extra={
                    "event_type": "playwright_context",
                    "cdp_url": self.cdp_url,
                },
            )
        else:
            profile_dir = str(Path(self.user_data_dir).expanduser().resolve())
            browser_channel = self.channel.strip() or None

            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel=browser_channel,
                headless=self.headless,
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1440, "height": 900},
            )
            self.logger.info(
                "playwright_persistent_context_ready",
                extra={
                    "event_type": "playwright_context",
                    "profile_dir": profile_dir,
                    "headless": self.headless,
                    "channel": browser_channel or "default",
                },
            )

        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
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

        if self._attached_via_cdp:
            self.logger.warning(
                "x_login_required_in_attached_chrome",
                extra={
                    "event_type": "x_auth",
                    "message_hint": "Please login to x.com in your existing Chrome window.",
                },
            )
            await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=self.timeout_ms)
        else:
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

    async def _goto_profile(self, page: Page, account: str) -> None:
        url = f"https://x.com/{account}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            await page.wait_for_selector("article", timeout=self.timeout_ms)
            posts_tab = page.get_by_role("tab", name="Posts", exact=True)
            if await posts_tab.count() > 0:
                await posts_tab.first.click(timeout=5_000)
                await page.wait_for_timeout(500)
        except Exception as exc:
            if PlaywrightTimeoutError is not None and isinstance(exc, PlaywrightTimeoutError):
                raise RuntimeError("playwright_timeout_loading_x_profile") from exc
            raise

    async def _extract_profile_rows(self, page: Page) -> list[dict]:
        try:
            rows = await page.eval_on_selector_all("article", _EXTRACT_ARTICLE_ROWS_JS)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
            return []
        except Exception as exc:
            if PlaywrightTimeoutError is not None and isinstance(exc, PlaywrightTimeoutError):
                raise RuntimeError("playwright_timeout_loading_x_profile") from exc
            raise

    async def _scroll_collect_tweets(
        self,
        page: Page,
        since_utc: datetime,
        max_scrolls: int,
        scroll_pause_ms: int,
    ) -> dict[str, TweetData]:
        collected: dict[str, TweetData] = {}
        stagnant_rounds = 0

        for scroll_idx in range(max_scrolls + 1):
            rows = await self._extract_profile_rows(page)
            new_in_round = 0
            for row in rows:
                tweet = _row_to_tweet(row)
                if tweet is None or tweet.tweet_id in collected:
                    continue
                collected[tweet.tweet_id] = tweet
                new_in_round += 1

            if collected:
                oldest = min(item.posted_at for item in collected.values())
                if oldest < since_utc:
                    self.logger.info(
                        "backfill_reached_since_boundary",
                        extra={
                            "event_type": "backfill",
                            "oldest_posted_at": oldest.isoformat(),
                            "since": since_utc.isoformat(),
                            "scroll_round": scroll_idx,
                            "collected": len(collected),
                        },
                    )
                    break

            if scroll_idx >= max_scrolls:
                break

            if new_in_round == 0:
                stagnant_rounds += 1
                if stagnant_rounds >= 3:
                    self.logger.info(
                        "backfill_stopped_no_new_tweets",
                        extra={
                            "event_type": "backfill",
                            "scroll_round": scroll_idx,
                            "collected": len(collected),
                        },
                    )
                    break
            else:
                stagnant_rounds = 0

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(max(scroll_pause_ms, 0))

        return collected


class MockTwitterClient:
    """In-memory fake Twitter client for deterministic tests."""

    def __init__(self) -> None:
        self._tweets: list[TweetData] = []

    def add_tweet(self, tweet: TweetData) -> None:
        self._tweets.insert(0, tweet)

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        return self._tweets[:limit]

    async def fetch_tweets_since(
        self,
        account: str,
        since: datetime,
        max_scrolls: int = 150,
        scroll_pause_ms: int = 1500,
    ) -> list[TweetData]:
        since_utc = _as_utc(since)
        return sorted(
            [tweet for tweet in self._tweets if _as_utc(tweet.posted_at) >= since_utc],
            key=lambda item: item.posted_at,
        )


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


def _row_to_tweet(row: dict) -> TweetData | None:
    href = row.get("href")
    tweet_id = _extract_status_id(href or "")
    if not tweet_id:
        return None

    text = (row.get("text") or "").strip()
    absolute_url = href if isinstance(href, str) and href.startswith("http") else f"https://x.com{href}"
    return TweetData(
        tweet_id=tweet_id,
        text=text,
        posted_at=_as_utc(_parse_x_datetime(row.get("datetime"))),
        is_reply=bool(row.get("isReply")),
        is_retweet=bool(row.get("isRetweet")),
        url=absolute_url,
    )


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


def _rows_to_tweets(rows: list[dict]) -> list[TweetData]:
    tweets: list[TweetData] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        href = row.get("href")
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
                posted_at=_as_utc(posted_at),
                is_reply=bool(row.get("isReply")),
                is_retweet=bool(row.get("isRetweet")),
                url=absolute_url,
            )
        )
    return tweets


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
