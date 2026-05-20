from __future__ import annotations

from datetime import datetime, timezone
import logging

import pytest

from app.ingestion.clients import FallbackTwitterClient, TweetData


class AlwaysFailClient:
    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        raise RuntimeError("backend_failed")


class AlwaysSuccessClient:
    async def fetch_recent_tweets(self, account: str, limit: int = 20) -> list[TweetData]:
        return [
            TweetData(
                tweet_id="123",
                text="adding NVDA",
                posted_at=datetime.now(timezone.utc),
                is_reply=False,
                is_retweet=False,
                url="https://x.com/example/status/123",
            )
        ]


@pytest.mark.asyncio
async def test_fallback_twitter_client_uses_second_backend() -> None:
    client = FallbackTwitterClient(
        clients=[
            ("first", AlwaysFailClient()),
            ("second", AlwaysSuccessClient()),
        ],
        logger=logging.getLogger("test-twitter-fallback"),
    )

    tweets = await client.fetch_recent_tweets(account="CKCapitalxx", limit=20)

    assert len(tweets) == 1
    assert tweets[0].tweet_id == "123"


@pytest.mark.asyncio
async def test_fallback_twitter_client_raises_if_all_backends_fail() -> None:
    client = FallbackTwitterClient(
        clients=[
            ("first", AlwaysFailClient()),
            ("second", AlwaysFailClient()),
        ],
        logger=logging.getLogger("test-twitter-fallback"),
    )

    with pytest.raises(RuntimeError, match="all_twitter_backends_failed"):
        await client.fetch_recent_tweets(account="CKCapitalxx", limit=20)
