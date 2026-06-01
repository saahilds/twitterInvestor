from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import inspect
import json

from app.config.settings import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.runtime import build_ingestion_service, build_logger, build_twitter_client


def _parse_since(value: str) -> datetime:
    normalized = value.strip()
    if len(normalized) == 10:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = build_logger(settings)
    init_db()

    twitter_client = build_twitter_client(settings, logger)
    ingestion_service = build_ingestion_service(
        settings=settings,
        twitter_client=twitter_client,
        session_factory=SessionLocal,
        logger=logger,
    )

    since = _parse_since(args.since)
    try:
        result = await ingestion_service.backfill_since(
            since=since,
            max_scrolls=args.max_scrolls,
            scroll_pause_ms=args.scroll_pause_ms,
        )
    finally:
        close_method = getattr(twitter_client, "close", None)
        if callable(close_method):
            close_result = close_method()
            if inspect.isawaitable(close_result):
                await close_result

    payload = {
        "account": settings.target_account,
        "since": result.since.isoformat(),
        "fetched": result.fetched,
        "inserted": result.inserted,
        "skipped_duplicate": result.skipped_duplicate,
        "skipped_filtered": result.skipped_filtered,
        "oldest_posted_at": result.oldest_posted_at.isoformat() if result.oldest_posted_at else None,
        "newest_posted_at": result.newest_posted_at.isoformat() if result.newest_posted_at else None,
    }
    print(json.dumps(payload, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical tweets for TARGET_ACCOUNT into trading_bot.db.",
    )
    parser.add_argument(
        "--since",
        default="2026-01-01",
        help="Include tweets posted on/after this date (YYYY-MM-DD or ISO-8601).",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=None,
        help="Profile scroll rounds (default: BACKFILL_MAX_SCROLLS from settings).",
    )
    parser.add_argument(
        "--scroll-pause-ms",
        type=int,
        default=None,
        help="Delay between scrolls in ms (default: BACKFILL_SCROLL_PAUSE_MS).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
