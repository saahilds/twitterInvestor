from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.clients import TweetData, TwitterClient
from app.models.db_models import Tweet
from app.models.schemas import IngestedTweet


@dataclass(slots=True)
class PersistStats:
    inserted: list[IngestedTweet]
    skipped_duplicate: int
    skipped_filtered: int


@dataclass(slots=True)
class BackfillResult:
    fetched: int
    inserted: int
    skipped_duplicate: int
    skipped_filtered: int
    since: datetime
    oldest_posted_at: datetime | None
    newest_posted_at: datetime | None


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
        feed_down_after_failures: int = 5,
    ) -> None:
        self.twitter_client = twitter_client
        self.session_factory = session_factory
        self.target_account = target_account
        self.fetch_limit = fetch_limit
        self.ignore_replies = ignore_replies
        self.ignore_retweets = ignore_retweets
        self.logger = logger
        self.alert_service = None
        self.feed_down_after_failures = max(1, feed_down_after_failures)
        self.consecutive_failures = 0
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self._feed_down_alerted = False

    async def poll(self) -> list[IngestedTweet]:
        """Fetch account tweets and persist only unseen messages."""
        try:
            payloads = await self.twitter_client.fetch_recent_tweets(
                account=self.target_account,
                limit=self.fetch_limit,
            )
        except Exception as exc:
            self.consecutive_failures += 1
            self.last_error = str(exc)
            self.logger.exception(
                "tweet_fetch_failed",
                extra={"error": str(exc), "consecutive_failures": self.consecutive_failures},
            )
            await self._maybe_alert_feed_down()
            return []
        await self._mark_fetch_succeeded()
        stats = self._persist_payloads(payloads)
        return stats.inserted

    async def _maybe_alert_feed_down(self) -> None:
        if self._feed_down_alerted or self.consecutive_failures < self.feed_down_after_failures:
            return
        if self.alert_service is None:
            return
        sent = await self.alert_service.send(
            "x_feed_down",
            {
                "error": self.last_error,
                "text": (
                    f"No tweets fetched for {self.consecutive_failures} polls in a row. "
                    "The bot keeps retrying and the watchdog restarts it if this continues. "
                    "Check the dashboard or the Chrome window on the PC."
                ),
            },
            key="x_feed_down",
        )
        self._feed_down_alerted = sent

    async def _mark_fetch_succeeded(self) -> None:
        failed_polls = self.consecutive_failures
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success_at = datetime.now(timezone.utc)
        if not self._feed_down_alerted:
            return
        self._feed_down_alerted = False
        if self.alert_service is not None:
            await self.alert_service.send(
                "x_feed_recovered",
                {"text": f"Fetching tweets again after {failed_polls} failed polls."},
                key="x_feed_recovered",
            )

    async def backfill_since(
        self,
        since: datetime,
        max_scrolls: int | None = None,
        scroll_pause_ms: int | None = None,
    ) -> BackfillResult:
        """Scroll the profile timeline and persist tweets posted on or after ``since``."""
        since_utc = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)

        self.logger.info(
            "backfill_started",
            extra={
                "event_type": "backfill",
                "account": self.target_account,
                "since": since_utc.isoformat(),
            },
        )

        try:
            payloads = await self.twitter_client.fetch_tweets_since(
                account=self.target_account,
                since=since_utc,
                max_scrolls=max_scrolls,
                scroll_pause_ms=scroll_pause_ms,
            )
        except Exception as exc:
            self.logger.exception("backfill_failed", extra={"error": str(exc)})
            raise

        persist_stats = self._persist_payloads(payloads, log_each_new=False)
        posted_times = [payload.posted_at for payload in payloads]

        result = BackfillResult(
            fetched=len(payloads),
            inserted=len(persist_stats.inserted),
            skipped_duplicate=persist_stats.skipped_duplicate,
            skipped_filtered=persist_stats.skipped_filtered,
            since=since_utc,
            oldest_posted_at=min(posted_times) if posted_times else None,
            newest_posted_at=max(posted_times) if posted_times else None,
        )

        self.logger.info(
            "backfill_completed",
            extra={
                "event_type": "backfill",
                "account": self.target_account,
                "since": since_utc.isoformat(),
                "fetched": result.fetched,
                "inserted": result.inserted,
                "skipped_duplicate": result.skipped_duplicate,
                "skipped_filtered": result.skipped_filtered,
            },
        )
        return result

    def _persist_payloads(
        self,
        payloads: list[TweetData],
        log_each_new: bool = True,
    ) -> PersistStats:
        new_tweets: list[IngestedTweet] = []
        skipped_duplicate = 0
        skipped_filtered = 0
        with self.session_factory() as db:
            for payload in sorted(payloads, key=lambda item: item.posted_at):
                if self.ignore_replies and payload.is_reply:
                    skipped_filtered += 1
                    continue
                if self.ignore_retweets and payload.is_retweet:
                    skipped_filtered += 1
                    continue

                exists = db.execute(
                    select(Tweet).where(Tweet.tweet_id == payload.tweet_id)
                ).scalar_one_or_none()
                if exists:
                    skipped_duplicate += 1
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
                if log_each_new:
                    self.logger.info(
                        "tweet_ingested",
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

        return PersistStats(
            inserted=new_tweets,
            skipped_duplicate=skipped_duplicate,
            skipped_filtered=skipped_filtered,
        )

    @staticmethod
    def _compact_text(text: str, limit: int = 240) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit]}..."
