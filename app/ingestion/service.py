from __future__ import annotations

import logging
from collections.abc import Callable
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.clients import TwitterClient
from app.models.db_models import Tweet
from app.models.schemas import IngestedTweet


class TweetIngestionService:
    """Polling ingestion service that stores new tweets and skips duplicates."""

    def __init__(
        self,
        twitter_client: TwitterClient,
        session_factory: Callable[[], Session],
        target_account: str,
        fetch_limit: int,
        ignore_replies: bool,
        ignore_retweets: bool,
        logger: logging.Logger,
    ) -> None:
        self.twitter_client = twitter_client
        self.session_factory = session_factory
        self.target_account = target_account
        self.fetch_limit = fetch_limit
        self.ignore_replies = ignore_replies
        self.ignore_retweets = ignore_retweets
        self.logger = logger

    async def poll(self) -> list[IngestedTweet]:
        """Fetch account tweets and persist only unseen messages."""
        try:
            payloads = await self.twitter_client.fetch_recent_tweets(
                account=self.target_account,
                limit=self.fetch_limit,
            )
        except Exception as exc:
            self.logger.exception("tweet_fetch_failed", extra={"error": str(exc)})
            return []

        new_tweets: list[IngestedTweet] = []
        with self.session_factory() as db:
            for payload in sorted(payloads, key=lambda item: item.posted_at):
                if self.ignore_replies and payload.is_reply:
                    continue
                if self.ignore_retweets and payload.is_retweet:
                    continue

                exists = db.execute(
                    select(Tweet).where(Tweet.tweet_id == payload.tweet_id)
                ).scalar_one_or_none()
                if exists:
                    continue

                tweet = Tweet(
                    tweet_id=payload.tweet_id,
                    account=self.target_account,
                    text=payload.text,
                    posted_at=payload.posted_at,
                    is_reply=payload.is_reply,
                    is_retweet=payload.is_retweet,
                    url=payload.url,
                )
                db.add(tweet)
                db.flush()

                new_tweets.append(
                    IngestedTweet(
                        tweet_pk=tweet.id,
                        tweet_id=tweet.tweet_id,
                        account=tweet.account,
                        text=tweet.text,
                        posted_at=tweet.posted_at,
                        fetched_at=tweet.fetched_at,
                        is_reply=tweet.is_reply,
                        is_retweet=tweet.is_retweet,
                    )
                )
                self.logger.info(
                    "new_tweet_detected",
                    extra={
                        "event_type": "tweet_ingested",
                        "tweet_id": tweet.tweet_id,
                        "account": tweet.account,
                        "posted_at": tweet.posted_at.isoformat(),
                        "is_reply": tweet.is_reply,
                        "is_retweet": tweet.is_retweet,
                        "tweet_text": self._compact_text(tweet.text),
                    },
                )
            db.commit()

        if new_tweets:
            self.logger.info(
                "new_tweets_ingested",
                extra={
                    "event_type": "ingestion_summary",
                    "count": len(new_tweets),
                    "account": self.target_account,
                },
            )
        return new_tweets

    @staticmethod
    def _compact_text(text: str, limit: int = 240) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit]}..."
