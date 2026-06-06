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
from app.execution.robinhood_session import RobinhoodSessionManager
from app.parsing.factory import build_signal_parser
from app.risk.risk_manager import RiskConfig, RiskManager
from app.runtime import build_ingestion_service, build_logger, build_twitter_client
from app.services.audit import ExecutionAuditLogger
from app.portfolio.quotes import QuoteProvider
from app.services.pnl_service import PnlService
from app.services.trade_status import TradeStatusSync
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

parser = build_signal_parser(settings)

risk_manager = RiskManager(
    RiskConfig(
        seed_tickers=set(settings.allowed_tickers),
        max_trade_size_usd=settings.max_trade_size_usd,
        default_trade_size_usd=settings.default_trade_size_usd,
        new_ticker_size_multiplier=settings.new_ticker_size_multiplier,
        cooldown_seconds=settings.cooldown_seconds,
        duplicate_window_seconds=settings.duplicate_window_seconds,
        trading_window_enabled=settings.trading_window_enabled,
        us_symbols_only=settings.us_symbols_only,
        max_trades_per_ticker_per_day=settings.max_trades_per_ticker_per_day,
        daily_limit_counts_simulation=settings.daily_limit_counts_simulation,
        live_trading_enabled=settings.live_trading_enabled,
    )
)

rh_session: RobinhoodSessionManager | None = None
if settings.broker_backend == "mock":
    broker = MockBroker()
else:
    rh_session = RobinhoodSessionManager(settings=settings, logger=logger)
    broker = RobinhoodBroker(settings=settings, logger=logger, session_manager=rh_session)
audit_logger = ExecutionAuditLogger(session_factory=SessionLocal, logger=logger)
trade_status_sync = TradeStatusSync(broker=broker, logger=logger) if isinstance(broker, RobinhoodBroker) else None
quote_provider = QuoteProvider(
    settings=settings,
    logger=logger,
    session_manager=rh_session,
)
pnl_service = PnlService(
    session_factory=SessionLocal,
    quote_provider=quote_provider,
    include_simulation=settings.pnl_include_simulation,
    quote_cache_seconds=settings.pnl_quote_cache_seconds,
)

worker = BotWorker(
    settings=settings,
    ingestion_service=ingestion_service,
    parser=parser,
    risk_manager=risk_manager,
    broker=broker,
    session_factory=SessionLocal,
    audit_logger=audit_logger,
    logger=logger,
    trade_status_sync=trade_status_sync,
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
app.include_router(
    create_router(
        session_factory=SessionLocal,
        worker=worker,
        settings=settings,
        trade_status_sync=trade_status_sync,
        pnl_service=pnl_service,
        broker=broker,
    )
)
