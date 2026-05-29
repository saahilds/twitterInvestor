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
from app.parsing.signal_parser import RuleBasedSignalParser
from app.risk.risk_manager import RiskConfig, RiskManager
from app.runtime import build_ingestion_service, build_logger, build_twitter_client
from app.services.audit import ExecutionAuditLogger
from app.services.worker import BotWorker

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
        trading_window_enabled=settings.trading_window_enabled,
        us_symbols_only=settings.us_symbols_only,
        max_trades_per_ticker_per_day=settings.max_trades_per_ticker_per_day,
        daily_limit_counts_simulation=settings.daily_limit_counts_simulation,
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
