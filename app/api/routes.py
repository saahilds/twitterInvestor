from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.execution.holdings import resolve_stocks_plus_cash
from app.execution.robinhood_broker import RobinhoodBroker
from app.models.db_models import ParsedSignal, RecognizedTicker, Trade, Tweet
from app.risk.market_hours import is_within_regular_market_hours
from app.models.schemas import (
    BrokerHoldingsSnapshot,
    ChartPointRead,
    DashboardSnapshot,
    DashboardTweetRead,
    HealthResponse,
    ParsedSignalRead,
    PortfolioChartResponse,
    PortfolioChartSummary,
    PortfolioPnlResponse,
    TradeChartAnnotationRead,
    RobinhoodHoldingRead,
    TradeRead,
    TweetRead,
    WorkerControlResponse,
)
from app.services import portfolio_history
from app.services.pnl_service import PnlService
from app.services.tweet_query import (
    DEFAULT_TWEET_LIMIT,
    DEFAULT_TWEET_RANGE,
    DEFAULT_TWEET_SIGNAL_FILTER,
    MAX_TWEET_LIMIT,
    TweetWindowError,
    fetch_dashboard_tweets,
    normalize_signal_filter,
    resolve_tweet_window,
)
from app.services.trade_status import TradeStatusSync
from app.services.worker import BotWorker


def create_router(
    session_factory: Callable[[], Session],
    worker: BotWorker,
    settings: Settings,
    trade_status_sync: TradeStatusSync | None = None,
    pnl_service: PnlService | None = None,
    broker: object | None = None,
) -> APIRouter:
    router = APIRouter()

    def _tweet_to_dashboard_read(tweet: Tweet) -> DashboardTweetRead:
        latest = None
        if tweet.parsed_signals:
            latest = max(tweet.parsed_signals, key=lambda signal: signal.created_at)
        payload = TweetRead.model_validate(tweet).model_dump()
        if latest is not None:
            payload.update(
                {
                    "signal_action": latest.action.value,
                    "signal_ticker": latest.ticker,
                    "signal_confidence": latest.confidence,
                    "signal_rejection_reason": latest.rejection_reason,
                }
            )
        return DashboardTweetRead(**payload)

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        snapshot = worker.snapshot()
        rh_logged_in = None
        rh_error = None
        rh_retry = None
        if isinstance(broker, RobinhoodBroker):
            session = broker.session_snapshot()
            rh_logged_in = session.logged_in
            rh_error = session.last_error
            rh_retry = session.retry_in_seconds if session.last_error else None
        return HealthResponse(
            worker_running=snapshot.running,
            worker_paused=snapshot.paused,
            simulation_mode=settings.simulation_mode,
            live_trading_enabled=settings.live_trading_enabled,
            order_execution_mode=settings.order_execution_mode,
            trading_window_enabled=settings.trading_window_enabled,
            within_market_hours=is_within_regular_market_hours(),
            target_account=settings.target_account,
            poll_interval_seconds=settings.poll_interval_seconds,
            dashboard_positions_refresh_seconds=settings.dashboard_positions_refresh_seconds,
            default_trade_size_usd=settings.default_trade_size_usd,
            robinhood_logged_in=rh_logged_in,
            robinhood_auth_error=rh_error,
            robinhood_auth_retry_in_seconds=rh_retry,
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

    @router.get("/trades/{trade_id}", response_model=TradeRead)
    async def get_trade(trade_id: int) -> TradeRead:
        with session_factory() as db:
            trade = db.get(Trade, trade_id)
            if trade is None:
                raise HTTPException(status_code=404, detail="trade_not_found")
        return TradeRead.model_validate(trade)

    @router.post("/trades/{trade_id}/refresh", response_model=TradeRead)
    async def refresh_trade(trade_id: int) -> TradeRead:
        if trade_status_sync is None:
            raise HTTPException(status_code=400, detail="trade_refresh_requires_robinhood_broker")
        with session_factory() as db:
            trade = db.get(Trade, trade_id)
            if trade is None:
                raise HTTPException(status_code=404, detail="trade_not_found")
            if trade.simulation:
                raise HTTPException(status_code=400, detail="cannot_refresh_simulated_trade")
            if not trade.broker_order_id:
                raise HTTPException(status_code=400, detail="trade_has_no_broker_order_id")
            trade = await trade_status_sync.refresh(db, trade)
            db.commit()
            db.refresh(trade)
        return TradeRead.model_validate(trade)

    @router.get("/portfolio/pnl", response_model=PortfolioPnlResponse)
    async def portfolio_pnl(
        live_only: bool = Query(default=False),
        no_live_prices: bool = Query(default=False),
    ) -> PortfolioPnlResponse:
        if pnl_service is None:
            raise HTTPException(status_code=503, detail="pnl_service_unavailable")
        include_simulation = settings.pnl_include_simulation and not live_only
        original_include = pnl_service.include_simulation
        pnl_service.include_simulation = include_simulation
        try:
            return pnl_service.build_report(fetch_live_prices=not no_live_prices)
        finally:
            pnl_service.include_simulation = original_include

    async def _fetch_broker_holdings(*, record_snapshot: bool = True) -> BrokerHoldingsSnapshot:
        if not settings.robinhood_username or not settings.robinhood_password:
            return BrokerHoldingsSnapshot(
                available=False,
                error="robinhood_credentials_missing",
            )
        if not isinstance(broker, RobinhoodBroker):
            return BrokerHoldingsSnapshot(
                available=False,
                error="broker_not_robinhood",
            )

        holdings, metrics, holdings_error = await asyncio.to_thread(broker.get_broker_snapshot)
        if holdings_error:
            return BrokerHoldingsSnapshot(
                available=False,
                account_number=broker._account_number,
                error=holdings_error,
            )

        rows = [
            RobinhoodHoldingRead(
                ticker=row.ticker,
                quantity=row.quantity,
                average_cost=row.average_cost,
                last_price=row.last_price,
                market_value=row.market_value,
                cost_basis=row.cost_basis,
                unrealized_pnl=row.unrealized_pnl,
                unrealized_pnl_pct=row.unrealized_pnl_pct,
            )
            for row in sorted(holdings, key=lambda row: row.market_value or 0.0, reverse=True)
        ]
        positions_market = sum(row.market_value or 0.0 for row in rows)
        total_unrealized = sum(row.unrealized_pnl or 0.0 for row in rows)
        portfolio_equity = metrics.portfolio_equity
        holdings_market = metrics.profile_market_value
        if holdings_market is None and rows:
            holdings_market = positions_market
        stocks_plus_cash = metrics.stocks_plus_cash or resolve_stocks_plus_cash(
            portfolio_equity=portfolio_equity,
            profile_market_value=holdings_market,
            cash=metrics.cash,
            positions_market_value=positions_market,
        )

        if record_snapshot and stocks_plus_cash is not None:
            with session_factory() as db:
                portfolio_history.record_snapshot(
                    db,
                    account_number=broker._account_number,
                    stocks_plus_cash=stocks_plus_cash,
                    holdings_market_value=holdings_market,
                    cash=metrics.cash,
                )

        return BrokerHoldingsSnapshot(
            available=True,
            account_number=broker._account_number,
            holdings=rows,
            portfolio_equity=round(portfolio_equity, 2) if portfolio_equity is not None else None,
            holdings_market_value=round(holdings_market, 2) if holdings_market is not None else None,
            positions_market_value=round(positions_market, 2) if rows else None,
            profile_market_value=(
                round(metrics.profile_market_value, 2) if metrics.profile_market_value is not None else None
            ),
            stocks_plus_cash=round(stocks_plus_cash, 2) if stocks_plus_cash is not None else None,
            cash=round(metrics.cash, 2) if metrics.cash is not None else None,
            total_market_value=round(stocks_plus_cash, 2) if stocks_plus_cash is not None else None,
            total_unrealized_pnl=round(total_unrealized, 2) if rows else None,
        )

    @router.get("/dashboard/chart", response_model=PortfolioChartResponse)
    async def dashboard_chart(
        range_key: str = Query(default="1w", alias="range"),
        live_only: bool = Query(default=False),
    ) -> PortfolioChartResponse:
        range_key = range_key if range_key in portfolio_history.RANGE_KEYS else "1w"
        current_value: float | None = None
        account_number: str | None = None

        if isinstance(broker, RobinhoodBroker):
            broker_holdings = await _fetch_broker_holdings(record_snapshot=False)
            if broker_holdings.available:
                current_value = broker_holdings.stocks_plus_cash
                account_number = broker_holdings.account_number

        with session_factory() as db:
            if current_value is None:
                current_value = portfolio_history.latest_snapshot_value(
                    db, account_number=account_number
                )
            points, annotations, source, window, summary = portfolio_history.build_chart_series(
                db,
                range_key=range_key,
                account_number=account_number,
                current_value=current_value,
                live_trades_only=live_only,
                ytd_baseline_usd=settings.chart_ytd_baseline_usd,
            )

        return PortfolioChartResponse(
            range=range_key,
            source=source,
            window_start=window.get("window_start"),
            window_end=window.get("window_end"),
            summary=PortfolioChartSummary.model_validate(summary),
            points=[ChartPointRead.model_validate(point) for point in points],
            annotations=[TradeChartAnnotationRead.model_validate(row) for row in annotations],
            session_open=window.get("session_open"),
            session_end=window.get("session_end"),
        )

    @router.get("/dashboard/tweets", response_model=list[DashboardTweetRead])
    async def dashboard_tweets(
        range_key: str = Query(default=DEFAULT_TWEET_RANGE, alias="range"),
        since: datetime | None = Query(default=None),
        until: datetime | None = Query(default=None),
        limit: int = Query(default=DEFAULT_TWEET_LIMIT, ge=1, le=MAX_TWEET_LIMIT),
        signal_filter: str = Query(default=DEFAULT_TWEET_SIGNAL_FILTER, alias="signal"),
    ) -> list[DashboardTweetRead]:
        try:
            since_dt, until_dt = resolve_tweet_window(
                range_key=range_key,
                since=since,
                until=until,
                now=datetime.now(timezone.utc),
            )
        except TweetWindowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        with session_factory() as db:
            rows = fetch_dashboard_tweets(
                db,
                since=since_dt,
                until=until_dt,
                limit=limit,
                signal_filter=normalize_signal_filter(signal_filter),
            )
        return [_tweet_to_dashboard_read(row) for row in rows]

    @router.get("/dashboard/data", response_model=DashboardSnapshot)
    async def dashboard_data(
        live_only: bool = Query(default=False),
        include_broker: bool = Query(default=True),
        fetch_live_pnl_prices: bool | None = Query(default=None),
    ) -> DashboardSnapshot:
        if pnl_service is None:
            raise HTTPException(status_code=503, detail="pnl_service_unavailable")

        snapshot = worker.snapshot()
        use_live_pnl_prices = (
            fetch_live_pnl_prices if fetch_live_pnl_prices is not None else include_broker
        )
        include_simulation = settings.pnl_include_simulation and not live_only
        original_include = pnl_service.include_simulation
        pnl_service.include_simulation = include_simulation
        try:
            pnl = pnl_service.build_report(fetch_live_prices=use_live_pnl_prices)
        finally:
            pnl_service.include_simulation = original_include

        with session_factory() as db:
            trades = db.execute(
                select(Trade).order_by(Trade.created_at.desc()).limit(15)
            ).scalars().all()
            recognized = db.execute(
                select(RecognizedTicker.ticker).order_by(RecognizedTicker.ticker.asc())
            ).scalars().all()

        if include_broker:
            broker_holdings = await _fetch_broker_holdings()
        else:
            broker_holdings = BrokerHoldingsSnapshot(available=False, error="positions_refresh_skipped")

        return DashboardSnapshot(
            health=HealthResponse(
                worker_running=snapshot.running,
                worker_paused=snapshot.paused,
                simulation_mode=settings.simulation_mode,
                live_trading_enabled=settings.live_trading_enabled,
                order_execution_mode=settings.order_execution_mode,
                trading_window_enabled=settings.trading_window_enabled,
                within_market_hours=is_within_regular_market_hours(),
                target_account=settings.target_account,
                poll_interval_seconds=settings.poll_interval_seconds,
                dashboard_positions_refresh_seconds=settings.dashboard_positions_refresh_seconds,
                default_trade_size_usd=settings.default_trade_size_usd,
            ),
            pnl=pnl,
            broker_holdings=broker_holdings,
            recent_tweets=[],
            recent_trades=[TradeRead.model_validate(row) for row in trades],
            recognized_tickers=[str(ticker) for ticker in recognized],
            worker_iteration_count=snapshot.iteration_count,
            worker_last_error=snapshot.last_error,
        )

    @router.get("/broker/holdings", response_model=BrokerHoldingsSnapshot)
    async def broker_holdings() -> BrokerHoldingsSnapshot:
        return await _fetch_broker_holdings()

    @router.get("/dashboard")
    async def dashboard() -> FileResponse:
        html_path = Path(__file__).resolve().parent / "dashboard.html"
        return FileResponse(html_path)

    @router.get("/dashboard/balance-chart.js")
    async def balance_chart_js() -> FileResponse:
        js_path = Path(__file__).resolve().parent / "balance-chart.js"
        return FileResponse(js_path, media_type="application/javascript")

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
