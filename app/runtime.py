from __future__ import annotations

import logging

from app.config.settings import Settings
from app.ingestion.clients import MockTwitterClient, PlaywrightTwitterClient, TwitterClient
from app.ingestion.service import TweetIngestionService
from app.utils.logging import configure_logging


def build_twitter_client(settings: Settings, logger: logging.Logger) -> TwitterClient:
    if settings.twitter_backend == "mock":
        return MockTwitterClient()

    return PlaywrightTwitterClient(
        timeout_ms=settings.playwright_timeout_ms,
        profile_load_retries=settings.playwright_profile_load_retries,
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


def build_ingestion_service(
    settings: Settings,
    twitter_client: TwitterClient,
    session_factory,
    logger: logging.Logger,
) -> TweetIngestionService:
    return TweetIngestionService(
        twitter_client=twitter_client,
        session_factory=session_factory,
        target_account=settings.target_account,
        fetch_limit=settings.fetch_limit,
        ignore_replies=settings.ignore_replies,
        ignore_retweets=settings.ignore_retweets,
        logger=logger,
    )


def build_logger(settings: Settings) -> logging.Logger:
    return configure_logging(settings)
