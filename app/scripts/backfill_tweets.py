from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.config.settings import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.ingestion.clients import PlaywrightTwitterClient
from app.models.db_models import Tweet
from app.utils.logging import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill tweets for a given year into SQLite.")
    parser.add_argument("--year", type=int, default=2026, help="Year to backfill, e.g. 2026")
    parser.add_argument(
        "--max-tweets",
        type=int,
        default=10_000,
        help="Maximum tweets to collect in the target year window",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=2_000,
        help="Maximum timeline scroll iterations",
    )
    parser.add_argument(
        "--include-replies",
        action="store_true",
        help="Include replies regardless of IGNORE_REPLIES setting",
    )
    parser.add_argument(
        "--include-retweets",
        action="store_true",
        help="Include reposts/retweets regardless of IGNORE_RETWEETS setting",
    )
    return parser.parse_args()


async def run_backfill(args: argparse.Namespace) -> None:
    settings = get_settings()
    logger = configure_logging(settings)
    init_db()

    start_at = datetime(args.year, 1, 1, tzinfo=timezone.utc)
    end_at = datetime(args.year + 1, 1, 1, tzinfo=timezone.utc)
    include_replies = args.include_replies or not settings.ignore_replies
    include_retweets = args.include_retweets or not settings.ignore_retweets

    client = PlaywrightTwitterClient(
        timeout_ms=settings.playwright_timeout_ms,
        headless=settings.playwright_headless,
        user_data_dir=settings.playwright_user_data_dir,
        channel=settings.playwright_channel,
        cdp_url=settings.playwright_cdp_url,
        require_login=settings.playwright_require_login,
        login_timeout_seconds=settings.playwright_login_timeout_seconds,
        backfill_max_scrolls=settings.backfill_max_scrolls,
        backfill_scroll_pause_ms=settings.backfill_scroll_pause_ms,
        logger=logger,
    )

    try:
        logger.info(
            "backfill_started",
            extra={
                "event_type": "backfill",
                "account": settings.target_account,
                "year": args.year,
                "max_tweets": args.max_tweets,
                "max_scrolls": args.max_scrolls,
            },
        )
        tweets = await client.fetch_tweets_between(
            account=settings.target_account,
            start_at=start_at,
            end_at=end_at,
            max_tweets=args.max_tweets,
            max_scrolls=args.max_scrolls,
        )
    finally:
        await client.close()

    inserted = 0
    skipped_existing = 0
    skipped_filtered = 0

    with SessionLocal() as db:
        existing_ids = set(
            db.execute(
                select(Tweet.tweet_id).where(Tweet.account == settings.target_account)
            )
            .scalars()
            .all()
        )
        for payload in tweets:
            if payload.tweet_id in existing_ids:
                skipped_existing += 1
                continue
            if payload.is_reply and not include_replies:
                skipped_filtered += 1
                continue
            if payload.is_retweet and not include_retweets:
                skipped_filtered += 1
                continue

            db.add(
                Tweet(
                    tweet_id=payload.tweet_id,
                    account=settings.target_account,
                    text=payload.text,
                    posted_at=payload.posted_at,
                    is_reply=payload.is_reply,
                    is_retweet=payload.is_retweet,
                    url=payload.url,
                )
            )
            existing_ids.add(payload.tweet_id)
            inserted += 1

        db.commit()

    logger.info(
        "backfill_completed",
        extra={
            "event_type": "backfill",
            "account": settings.target_account,
            "year": args.year,
            "fetched": len(tweets),
            "inserted": inserted,
            "skipped_existing": skipped_existing,
            "skipped_filtered": skipped_filtered,
        },
    )


def main() -> None:
    args = parse_args()
    asyncio.run(run_backfill(args))


if __name__ == "__main__":
    main()
