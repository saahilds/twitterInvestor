from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Protocol

try:
    import snscrape.modules.twitter as sntwitter
except Exception:  # pragma: no cover - runtime environment dependent
    sntwitter = None

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - runtime environment dependent
    PlaywrightTimeoutError = None
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


class SnscrapeTwitterClient:
    """Simple snscrape-backed implementation for public account polling."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        return await asyncio.to_thread(self._fetch_sync, account, limit)

    def _fetch_sync(self, account: str, limit: int) -> list[TweetData]:
        if sntwitter is None:
            raise RuntimeError("snscrape is unavailable in this environment")

        try:
            scraper = sntwitter.TwitterUserScraper(account)
            tweets: list[TweetData] = []
            for item in scraper.get_items():
                tweets.append(
                    TweetData(
                        tweet_id=str(item.id),
                        text=item.rawContent,
                        posted_at=item.date,
                        is_reply=item.inReplyToTweetId is not None,
                        is_retweet=item.retweetedTweet is not None,
                        url=item.url,
                    )
                )
                if len(tweets) >= limit:
                    break
            return tweets
        except Exception as exc:
            if _looks_like_snscrape_graphql_404(exc):
                raise RuntimeError("snscrape_blocked_graphql_404") from exc
            raise


class PlaywrightTwitterClient:
    """Playwright-backed public X profile reader used as fallback path."""

    status_href_pattern = re.compile(r"/status/(\d+)")

    def __init__(self, timeout_ms: int = 20_000, headless: bool = True) -> None:
        self.timeout_ms = timeout_ms
        self.headless = headless

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        if async_playwright is None:
            raise RuntimeError("playwright is unavailable in this environment")

        url = f"https://x.com/{account}"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()
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
            except Exception as exc:
                if PlaywrightTimeoutError is not None and isinstance(exc, PlaywrightTimeoutError):
                    raise RuntimeError("playwright_timeout_loading_x_profile") from exc
                raise
            finally:
                await context.close()
                await browser.close()

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


class FallbackTwitterClient:
    """Try multiple Twitter clients in order until one succeeds."""

    def __init__(self, clients: list[tuple[str, TwitterClient]], logger: logging.Logger) -> None:
        self.clients = clients
        self.logger = logger

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        errors: list[str] = []
        for backend_name, client in self.clients:
            try:
                tweets = await client.fetch_recent_tweets(account=account, limit=limit)
                self.logger.info(
                    "twitter_backend_success",
                    extra={
                        "event_type": "twitter_backend_success",
                        "backend": backend_name,
                        "count": len(tweets),
                    },
                )
                return tweets
            except Exception as exc:
                errors.append(f"{backend_name}:{exc}")
                self.logger.warning(
                    "twitter_backend_failed",
                    extra={
                        "event_type": "twitter_backend_failed",
                        "backend": backend_name,
                        "error": str(exc),
                    },
                )

        raise RuntimeError("all_twitter_backends_failed: " + "; ".join(errors))


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


def _looks_like_snscrape_graphql_404(exc: Exception) -> bool:
    text = str(exc).lower()
    return "graphql" in text and "404" in text
