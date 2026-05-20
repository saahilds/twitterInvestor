from datetime import datetime, timezone
import logging

import pytest
from sqlalchemy import select

from app.ingestion.clients import MockTwitterClient, TweetData
from app.ingestion.service import TweetIngestionService
from app.models.db_models import Tweet


@pytest.mark.asyncio
async def test_ingestion_deduplicates_tweets(session_factory) -> None:
    client = MockTwitterClient()
    client.add_tweet(
        TweetData(
            tweet_id="100",
            text="adding NVDA",
            posted_at=datetime.now(timezone.utc),
            is_reply=False,
            is_retweet=False,
            url="https://x.com/example/100",
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

    first = await service.poll()
    second = await service.poll()

    assert len(first) == 1
    assert len(second) == 0

    with session_factory() as db:
        rows = db.execute(select(Tweet)).scalars().all()
    assert len(rows) == 1
