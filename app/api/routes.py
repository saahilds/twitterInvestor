from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.db_models import ParsedSignal, Trade, Tweet
from app.risk.market_hours import is_within_regular_market_hours
from app.models.schemas import (
    HealthResponse,
    ParsedSignalRead,
    TradeRead,
    TweetRead,
    WorkerControlResponse,
)
from app.services.worker import BotWorker


def create_router(
    session_factory: Callable[[], Session],
    worker: BotWorker,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        snapshot = worker.snapshot()
        return HealthResponse(
            worker_running=snapshot.running,
            worker_paused=snapshot.paused,
            simulation_mode=settings.simulation_mode,
            live_trading_enabled=settings.live_trading_enabled,
            order_execution_mode=settings.order_execution_mode,
            trading_window_enabled=settings.trading_window_enabled,
            within_market_hours=is_within_regular_market_hours(),
            target_account=settings.target_account,
        )

    @router.get("/tweets", response_model=list[TweetRead])
    async def list_tweets(limit: int = Query(default=50, ge=1, le=200)) -> list[TweetRead]:
        with session_factory() as db:
            rows = db.execute(
                select(Tweet).order_by(Tweet.fetched_at.desc()).limit(limit)
            ).scalars().all()
        return [TweetRead.model_validate(row) for row in rows]

    @router.get("/signals", response_model=list[ParsedSignalRead])
    async def list_signals(limit: int = Query(default=50, ge=1, le=200)) -> list[ParsedSignalRead]:
        with session_factory() as db:
            rows = db.execute(
                select(ParsedSignal).order_by(ParsedSignal.created_at.desc()).limit(limit)
            ).scalars().all()
        return [ParsedSignalRead.model_validate(row) for row in rows]

    @router.get("/trades", response_model=list[TradeRead])
    async def list_trades(limit: int = Query(default=50, ge=1, le=200)) -> list[TradeRead]:
        with session_factory() as db:
            rows = db.execute(
                select(Trade).order_by(Trade.created_at.desc()).limit(limit)
            ).scalars().all()
        return [TradeRead.model_validate(row) for row in rows]

    @router.post("/pause", response_model=WorkerControlResponse)
    async def pause_worker() -> WorkerControlResponse:
        worker.pause()
        snapshot = worker.snapshot()
        return WorkerControlResponse(
            running=snapshot.running,
            paused=snapshot.paused,
            message="worker paused",
        )

    @router.post("/resume", response_model=WorkerControlResponse)
    async def resume_worker() -> WorkerControlResponse:
        worker.resume()
        snapshot = worker.snapshot()
        return WorkerControlResponse(
            running=snapshot.running,
            paused=snapshot.paused,
            message="worker resumed",
        )

    return router
