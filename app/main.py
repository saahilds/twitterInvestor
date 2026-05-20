from __future__ import annotations

from contextlib import asynccontextmanager
import inspect

from fastapi import FastAPI

from app.api.routes import create_router
from app.config.settings import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.execution.mock_broker import MockBroker
from app.execution.robinhood_broker import RobinhoodBroker
from app.ingestion.clients import (
    MockTwitterClient,
    PlaywrightTwitterClient,
    TwitterClient,
)
from app.ingestion.service import TweetIngestionService
from app.parsing.signal_parser import RuleBasedSignalParser
from app.risk.risk_manager import RiskConfig, RiskManager
from app.services.audit import ExecutionAuditLogger
from app.services.worker import BotWorker
from app.utils.logging import configure_logging

settings = get_settings()
logger = configure_logging(settings)

init_db()

def build_twitter_client() -> TwitterClient:
    if settings.twitter_backend == "mock":
        return MockTwitterClient()

    return PlaywrightTwitterClient(
        timeout_ms=settings.playwright_timeout_ms,
        headless=settings.playwright_headless,
        user_data_dir=settings.playwright_user_data_dir,
        channel=settings.playwright_channel,
        require_login=settings.playwright_require_login,
        login_timeout_seconds=settings.playwright_login_timeout_seconds,
        logger=logger,
    )


twitter_client = build_twitter_client()
ingestion_service = TweetIngestionService(
    twitter_client=twitter_client,
    session_factory=SessionLocal,
    target_account=settings.target_account,
    fetch_limit=settings.fetch_limit,
    ignore_replies=settings.ignore_replies,
    ignore_retweets=settings.ignore_retweets,
    logger=logger,
)

parser = RuleBasedSignalParser(
    known_tickers=settings.allowed_tickers,
    default_trade_size_usd=settings.default_trade_size_usd,
)

risk_manager = RiskManager(
    RiskConfig(
        allowlist=set(settings.allowed_tickers),
        max_trade_size_usd=settings.max_trade_size_usd,
        default_trade_size_usd=settings.default_trade_size_usd,
        cooldown_seconds=settings.cooldown_seconds,
        duplicate_window_seconds=settings.duplicate_window_seconds,
    )
)

broker = MockBroker() if settings.broker_backend == "mock" else RobinhoodBroker(settings=settings, logger=logger)
audit_logger = ExecutionAuditLogger(session_factory=SessionLocal, logger=logger)

worker = BotWorker(
    settings=settings,
    ingestion_service=ingestion_service,
    parser=parser,
    risk_manager=risk_manager,
    broker=broker,
    session_factory=SessionLocal,
    audit_logger=audit_logger,
    logger=logger,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    yield
    await worker.stop()
    close_method = getattr(twitter_client, "close", None)
    if callable(close_method):
        close_result = close_method()
        if inspect.isawaitable(close_result):
            await close_result


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(create_router(session_factory=SessionLocal, worker=worker, settings=settings))
