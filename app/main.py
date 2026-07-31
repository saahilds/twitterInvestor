from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import inspect

from fastapi import FastAPI

from app.api.routes import create_router
from app.config.account_managers import default_manager_id, parse_bot_managers
from app.config.settings import get_settings
from app.db.init_db import init_db
from app.db.manager_repair import repair_manager_ids
from app.db.session import SessionLocal
from app.execution.mock_broker import MockBroker
from app.execution.robinhood_broker import RobinhoodBroker
from app.execution.robinhood_session import RobinhoodSessionManager
from app.parsing.factory import build_signal_parser
from app.risk.risk_manager import RiskConfig, RiskManager
from app.runtime import build_ingestion_service, build_logger, build_twitter_client
from app.services.account_manager import AccountManager
from app.services.alerts import AlertService
from app.services.audit import ExecutionAuditLogger
from app.services.daily_digest import DailyDigestService
from app.services.watchlist import WatchlistRegistry
from app.portfolio.quotes import QuoteProvider
from app.services.pnl_service import PnlService
from app.services.trade_status import TradeStatusSync
from app.services.worker import BotOrchestrator

settings = get_settings()
logger = build_logger(settings)

init_db()

alert_service = AlertService(
    webhook_url=settings.alert_webhook_url,
    enabled=settings.alert_enabled,
    on_live_trades=settings.alert_on_live_trades,
    on_rejected_signals=settings.alert_on_rejected_signals,
    rejected_reasons=settings.alert_rejected_reasons,
    on_worker_errors=settings.alert_on_worker_errors,
    cooldown_seconds=settings.alert_cooldown_seconds,
    logger=logger,
)
digest_service = DailyDigestService(SessionLocal)

twitter_client = build_twitter_client(settings, logger)
ingestion_service = build_ingestion_service(
    settings=settings,
    twitter_client=twitter_client,
    session_factory=SessionLocal,
    logger=logger,
)
if hasattr(ingestion_service, "alert_service"):
    ingestion_service.alert_service = alert_service

parser = build_signal_parser(settings)
audit_logger = ExecutionAuditLogger(session_factory=SessionLocal, logger=logger)

risk_config = RiskConfig(
    seed_tickers=set(settings.allowed_tickers),
    default_buy_allocation_pct=settings.default_buy_allocation_pct,
    max_buy_allocation_pct=settings.max_buy_allocation_pct,
    standard_buy_allocation_pct_max=settings.standard_buy_allocation_pct_max,
    reload_buy_allocation_pct_max=settings.reload_buy_allocation_pct_max,
    thesis_buy_allocation_pct_min=settings.thesis_buy_allocation_pct_min,
    thesis_buy_allocation_pct_max=settings.thesis_buy_allocation_pct_max,
    min_trade_notional_pct=settings.min_trade_notional_pct,
    min_trade_notional_usd=settings.min_trade_notional_usd,
    cash_buffer_pct=settings.cash_buffer_pct,
    max_sell_notional_pct=settings.max_sell_notional_pct,
    simulation_portfolio_usd=settings.simulation_portfolio_usd,
    new_ticker_size_multiplier=settings.new_ticker_size_multiplier,
    cooldown_seconds=settings.cooldown_seconds,
    duplicate_window_seconds=settings.duplicate_window_seconds,
    trading_window_enabled=settings.trading_window_enabled,
    us_symbols_only=settings.us_symbols_only,
    max_trades_per_ticker_per_day=settings.max_trades_per_ticker_per_day,
    daily_limit_counts_simulation=settings.daily_limit_counts_simulation,
    live_trading_enabled=settings.live_trading_enabled,
    min_buy_confidence_unlisted=settings.min_buy_confidence_unlisted,
    min_sell_notional_usd=settings.min_sell_notional_usd,
    watchlist_stale_days=settings.watchlist_stale_days,
    watchlist_max_conviction_score=settings.watchlist_max_conviction_score,
)

watchlist_registry = WatchlistRegistry(
    max_conviction_score=settings.watchlist_max_conviction_score,
    stale_days=settings.watchlist_stale_days,
)

manager_configs = parse_bot_managers(settings)
rh_session: RobinhoodSessionManager | None = None
account_managers: list[AccountManager] = []
brokers_by_manager: dict[str, RobinhoodBroker | MockBroker] = {}
trade_status_by_manager: dict[str, TradeStatusSync] = {}

if settings.broker_backend == "mock":
    for cfg in manager_configs:
        broker = MockBroker()
        brokers_by_manager[cfg.id] = broker
        account_managers.append(
            AccountManager(
                config=cfg,
                settings=settings,
                broker=broker,
                risk_manager=RiskManager(risk_config, watchlist=watchlist_registry),
                session_factory=SessionLocal,
                logger=logger,
                trade_status_sync=None,
                alert_service=alert_service,
            )
        )
else:
    rh_session = RobinhoodSessionManager(settings=settings, logger=logger, alert_service=alert_service)
    for cfg in manager_configs:
        broker = RobinhoodBroker(
            settings=settings,
            logger=logger,
            session_manager=rh_session,
            account_selector=cfg.robinhood_account,
        )
        brokers_by_manager[cfg.id] = broker
        trade_status = TradeStatusSync(broker=broker, logger=logger)
        trade_status_by_manager[cfg.id] = trade_status
        account_managers.append(
            AccountManager(
                config=cfg,
                settings=settings,
                broker=broker,
                risk_manager=RiskManager(risk_config, watchlist=watchlist_registry),
                session_factory=SessionLocal,
                logger=logger,
                trade_status_sync=trade_status,
                alert_service=alert_service,
            )
        )

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

orchestrator = BotOrchestrator(
    settings=settings,
    ingestion_service=ingestion_service,
    parser=parser,
    managers=account_managers,
    session_factory=SessionLocal,
    audit_logger=audit_logger,
    logger=logger,
    alert_service=alert_service,
    digest_service=digest_service,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.broker_backend == "robinhood":
        manager_to_account: dict[str, str] = {}
        for cfg in manager_configs:
            broker = brokers_by_manager.get(cfg.id)
            if isinstance(broker, RobinhoodBroker):
                login_error = await asyncio.to_thread(broker._ensure_live_session)
                if login_error is None and broker._account_number:
                    manager_to_account[cfg.id] = broker._account_number
        if manager_to_account:
            repaired = await asyncio.to_thread(
                repair_manager_ids,
                SessionLocal,
                manager_to_account=manager_to_account,
                legacy_manager=default_manager_id(settings, manager_configs),
            )
            if repaired:
                logger.info(
                    "manager_ids_repaired",
                    extra={"event_type": "startup", "rows_updated": repaired},
                )

    orchestrator.start()
    yield
    await orchestrator.stop()
    close_method = getattr(twitter_client, "close", None)
    if callable(close_method):
        close_result = close_method()
        if inspect.isawaitable(close_result):
            await close_result


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(
    create_router(
        session_factory=SessionLocal,
        orchestrator=orchestrator,
        settings=settings,
        manager_configs=manager_configs,
        trade_status_by_manager=trade_status_by_manager,
        pnl_service=pnl_service,
        brokers_by_manager=brokers_by_manager,
        rh_session=rh_session,
        digest_service=digest_service,
    )
)
