from datetime import datetime, timezone
import logging

import pytest
from sqlalchemy import select

from app.ingestion.clients import MockTwitterClient, TweetData
from app.ingestion.service import TweetIngestionService
from app.models.db_models import Tweet


@pytest.mark.asyncio
async def test_backfill_since_persists_and_deduplicates(session_factory) -> None:
    client = MockTwitterClient()
    client.add_tweet(
        TweetData(
            tweet_id="2026-1",
            text="adding NVDA",
            posted_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            is_reply=False,
            is_retweet=False,
            url="https://x.com/example/2026-1",
        )
    )
    client.add_tweet(
        TweetData(
            tweet_id="2025-old",
            text="old tweet",
            posted_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
            is_reply=False,
            is_retweet=False,
            url="https://x.com/example/2025-old",
        )
    )

    service = TweetIngestionService(
        twitter_client=client,
        session_factory=session_factory,
        target_account="CKCapitalxx",
        fetch_limit=20,
        ignore_replies=True,
        ignore_retweets=True,
        logger=logging.getLogger("test"),
    )

    first = await service.backfill_since(datetime(2026, 1, 1, tzinfo=timezone.utc))
    second = await service.backfill_since(datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert first.fetched == 1
    assert first.inserted == 1
    assert second.inserted == 0
    assert second.skipped_duplicate == 1

    with session_factory() as db:
        rows = db.execute(select(Tweet)).scalars().all()
    assert len(rows) == 1
    assert rows[0].tweet_id == "2026-1"
