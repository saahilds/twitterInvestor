from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

try:
    import snscrape.modules.twitter as sntwitter
except Exception:  # pragma: no cover - runtime environment dependent
    sntwitter = None


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

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        return await asyncio.to_thread(self._fetch_sync, account, limit)

    def _fetch_sync(self, account: str, limit: int) -> list[TweetData]:
        if sntwitter is None:
            raise RuntimeError("snscrape is unavailable in this environment")

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


class MockTwitterClient:
    """In-memory fake Twitter client for deterministic tests."""

    def __init__(self) -> None:
        self._tweets: list[TweetData] = []

    def add_tweet(self, tweet: TweetData) -> None:
        self._tweets.insert(0, tweet)

    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        return self._tweets[:limit]
