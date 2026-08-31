from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config.account_managers import AccountManagerConfig, DEFAULT_MANAGER_ID, default_manager_id
from app.config.settings import Settings
from app.execution.holdings import resolve_stocks_plus_cash
from app.execution.robinhood_auth_age import compute_auth_age
from app.execution.robinhood_broker import RobinhoodBroker
from app.execution.robinhood_session import RobinhoodSessionManager
from app.models.db_models import (
    ParsedSignal,
    ParserFeedback,
    RecognizedTicker,
    SignalAction,
    Trade,
    Tweet,
    TweetLabel,
    WatchlistEntry,
    utc_now,
)
from app.parsing.buy_conviction import infer_buy_conviction
from app.risk.market_hours import is_within_regular_market_hours
from app.models.schemas import (
    BrokerHoldingsSnapshot,
    ChartPointRead,
    DailyDigestRead,
    DashboardSnapshot,
    DashboardTweetRead,
    HealthResponse,
    ParsedSignalRead,
    ParserFeedbackCreate,
    ParserFeedbackRead,
    ReviewQueueItem,
    ReviewQueueLabelCreate,
    ReviewQueueLabelRead,
    RobinhoodReauthStatusResponse,
    PortfolioChartResponse,
    PortfolioChartSummary,
    PortfolioPnlResponse,
    TradeChartAnnotationRead,
    RobinhoodHoldingRead,
    TradeRead,
    WatchlistEntryRead,
    TweetRead,
    WorkerControlResponse,
)
from app.services import portfolio_history
from app.services.daily_digest import DailyDigestService
from app.services.pnl_service import PnlService
from app.services.tweet_query import (
    DEFAULT_TWEET_LIMIT,
    DEFAULT_TWEET_RANGE,
    DEFAULT_TWEET_SIGNAL_FILTER,
    DEFAULT_TWEET_SORT,
    DEFAULT_TWEET_TRADED_FILTER,
    MAX_TWEET_LIMIT,
    TweetWindowError,
    fetch_dashboard_tweets,
    normalize_signal_filter,
    normalize_ticker_prefix,
    normalize_traded_filter,
    normalize_tweet_sort,
    resolve_tweet_window,
)
from app.services.trade_query import (
    DEFAULT_TRADE_ACTION_FILTER,
    DEFAULT_TRADE_LIMIT,
    DEFAULT_TRADE_MODE_FILTER,
    DEFAULT_TRADE_SORT,
    DEFAULT_TRADE_STATUS_FILTER,
    MAX_TRADE_LIMIT,
    fetch_dashboard_trades,
    normalize_trade_action_filter,
    normalize_trade_mode_filter,
    normalize_trade_sort,
    normalize_trade_status_filter,
)
from app.services.trade_status import TradeStatusSync
from app.services.worker import BotOrchestrator


def create_router(
    session_factory: Callable[[], Session],
    orchestrator: BotOrchestrator,
    settings: Settings,
    manager_configs: list[AccountManagerConfig],
    trade_status_by_manager: dict[str, TradeStatusSync],
    pnl_service: PnlService | None = None,
    brokers_by_manager: dict[str, object] | None = None,
    rh_session: RobinhoodSessionManager | None = None,
    digest_service: DailyDigestService | None = None,
) -> APIRouter:
    router = APIRouter()
    brokers_by_manager = brokers_by_manager or {}
    manager_ids = [cfg.id for cfg in manager_configs]
    digest_service = digest_service or DailyDigestService(session_factory)

    default_manager = default_manager_id(settings, manager_configs)

    def _resolve_manager_id(manager: str | None) -> str:
        if manager and manager in manager_ids:
            return manager
        return default_manager

    def _tweet_to_dashboard_read(
        tweet: Tweet,
        feedback_map: dict[str, str] | None = None,
    ) -> DashboardTweetRead:
        latest = None
        if tweet.parsed_signals:
            latest = max(tweet.parsed_signals, key=lambda signal: signal.created_at)
        payload = TweetRead.model_validate(tweet).model_dump()
        traded = False
        trade_status = None
        trade_amount = None
        buy_conviction = None
        if latest is not None:
            trades = list(latest.trades) if latest.trades else []
            if not trades:
                trades = [t for s in tweet.parsed_signals for t in s.trades]
            if trades:
                trade = max(trades, key=lambda row: row.created_at)
                traded = True
                trade_status = trade.status
                trade_amount = trade.amount_usd
            if latest.action == SignalAction.BUY:
                buy_conviction = infer_buy_conviction(latest.raw_text or tweet.text).value
            payload.update(
                {
                    "signal_action": latest.action.value,
                    "signal_ticker": latest.ticker,
                    "signal_confidence": latest.confidence,
                    "signal_rejection_reason": latest.rejection_reason,
                    "buy_conviction": buy_conviction,
                    "traded": traded,
                    "trade_status": trade_status,
                    "trade_amount_usd": trade_amount,
                }
            )
        if feedback_map and tweet.tweet_id in feedback_map:
            payload["feedback_correct_action"] = feedback_map[tweet.tweet_id]
        return DashboardTweetRead(**payload)

    def _feedback_map(db: Session) -> dict[str, str]:
        rows = db.execute(select(ParserFeedback).order_by(ParserFeedback.created_at.desc()).limit(500)).scalars().all()
        return {row.tweet_id: row.correct_action.value for row in rows}

    def _session_snapshot():
        if rh_session is not None:
            return rh_session.snapshot()
        for broker in brokers_by_manager.values():
            if isinstance(broker, RobinhoodBroker):
                return broker.session_snapshot()
        return None

    def _auth_age_fields() -> dict:
        if settings.broker_backend != "robinhood":
            return {
                "robinhood_auth_last_at": None,
                "robinhood_auth_age_days": None,
                "robinhood_auth_days_remaining": None,
                "robinhood_auth_refresh_due_at": None,
                "robinhood_auth_status": None,
                "robinhood_reauth_status": None,
            }
        age = compute_auth_age(
            max_age_days=settings.robinhood_pickle_max_age_days,
            warn_days=settings.robinhood_pickle_warn_days,
        )
        reauth_status = None
        if rh_session is not None:
            reauth_status = rh_session.reauth_snapshot().status
        return {
            "robinhood_auth_last_at": age.last_authenticated_at,
            "robinhood_auth_age_days": age.age_days,
            "robinhood_auth_days_remaining": age.days_remaining,
            "robinhood_auth_refresh_due_at": age.refresh_due_at,
            "robinhood_auth_status": age.status,
            "robinhood_reauth_status": reauth_status,
        }

    def _health_response(*, active_manager: str, snapshot) -> HealthResponse:
        session = _session_snapshot()
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
            default_buy_allocation_pct=settings.default_buy_allocation_pct,
            robinhood_logged_in=session.logged_in if session is not None else None,
            robinhood_auth_error=session.last_error if session is not None else None,
            robinhood_auth_retry_in_seconds=(
                session.retry_in_seconds if session is not None and session.last_error else None
            ),
            managers=snapshot.managers,
            active_manager=active_manager,
            **_auth_age_fields(),
        )

    @router.get("/health", response_model=HealthResponse)
    async def health(manager: str | None = Query(default=None)) -> HealthResponse:
        snapshot = orchestrator.snapshot()
        active_manager = _resolve_manager_id(manager)
        return _health_response(active_manager=active_manager, snapshot=snapshot)

    @router.post("/robinhood/reauth", response_model=RobinhoodReauthStatusResponse)
    async def start_robinhood_reauth() -> RobinhoodReauthStatusResponse:
        if settings.broker_backend != "robinhood":
            raise HTTPException(status_code=400, detail="broker_backend_is_not_robinhood")
        if rh_session is None:
            raise HTTPException(status_code=503, detail="robinhood_session_unavailable")
        if not settings.robinhood_username or not settings.robinhood_password:
            raise HTTPException(status_code=400, detail="missing_robinhood_credentials")
        job = await asyncio.to_thread(rh_session.start_reauth)
        return RobinhoodReauthStatusResponse(
            status=job.status,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error=job.error,
            message=job.message,
        )

    @router.get("/robinhood/reauth/status", response_model=RobinhoodReauthStatusResponse)
    async def robinhood_reauth_status() -> RobinhoodReauthStatusResponse:
        if settings.broker_backend != "robinhood":
            raise HTTPException(status_code=400, detail="broker_backend_is_not_robinhood")
        if rh_session is None:
            raise HTTPException(status_code=503, detail="robinhood_session_unavailable")
        job = rh_session.reauth_snapshot()
        return RobinhoodReauthStatusResponse(
            status=job.status,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error=job.error,
            message=job.message,
        )

    @router.get("/tweets", response_model=list[TweetRead])
    async def list_tweets(limit: int = Query(default=50, ge=1, le=200)) -> list[TweetRead]:
        with session_factory() as db:
            rows = db.execute(
                select(Tweet).order_by(Tweet.fetched_at.desc()).limit(limit)
            ).scalars().all()
        return [TweetRead.model_validate(row) for row in rows]

    @router.get("/signals", response_model=list[ParsedSignalRead])
    async def list_signals(
        limit: int = Query(default=50, ge=1, le=200),
        manager: str | None = Query(default=None),
    ) -> list[ParsedSignalRead]:
        manager_id = _resolve_manager_id(manager)
        with session_factory() as db:
            rows = db.execute(
                select(ParsedSignal)
                .where(ParsedSignal.manager_id == manager_id)
                .order_by(ParsedSignal.created_at.desc())
                .limit(limit)
            ).scalars().all()
        return [ParsedSignalRead.model_validate(row) for row in rows]

    @router.get("/watchlist", response_model=list[WatchlistEntryRead])
    async def list_watchlist(
        manager: str | None = Query(default=None),
    ) -> list[WatchlistEntryRead]:
        manager_id = _resolve_manager_id(manager)
        with session_factory() as db:
            rows = db.execute(
                select(WatchlistEntry)
                .where(WatchlistEntry.manager_id == manager_id)
                .order_by(WatchlistEntry.conviction_score.desc(), WatchlistEntry.last_seen_at.desc())
            ).scalars().all()
        return [WatchlistEntryRead.model_validate(row) for row in rows]

    @router.get("/trades", response_model=list[TradeRead])
    async def list_trades(
        limit: int = Query(default=50, ge=1, le=200),
        manager: str | None = Query(default=None),
    ) -> list[TradeRead]:
        manager_id = _resolve_manager_id(manager)
        with session_factory() as db:
            rows = db.execute(
                select(Trade)
                .where(Trade.manager_id == manager_id)
                .order_by(Trade.created_at.desc())
                .limit(limit)
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
        with session_factory() as db:
            trade = db.get(Trade, trade_id)
            if trade is None:
                raise HTTPException(status_code=404, detail="trade_not_found")
            trade_status_sync = trade_status_by_manager.get(trade.manager_id)
            if trade_status_sync is None:
                raise HTTPException(status_code=400, detail="trade_refresh_requires_robinhood_broker")
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
        manager: str | None = Query(default=None),
    ) -> PortfolioPnlResponse:
        if pnl_service is None:
            raise HTTPException(status_code=503, detail="pnl_service_unavailable")
        manager_id = _resolve_manager_id(manager)
        include_simulation = settings.pnl_include_simulation and not live_only
        original_include = pnl_service.include_simulation
        pnl_service.include_simulation = include_simulation
        try:
            return pnl_service.build_report(
                fetch_live_prices=not no_live_prices,
                manager_id=manager_id,
            )
        finally:
            pnl_service.include_simulation = original_include

    async def _fetch_broker_holdings(
        *,
        manager_id: str,
        record_snapshot: bool = True,
    ) -> BrokerHoldingsSnapshot:
        if not settings.robinhood_username or not settings.robinhood_password:
            return BrokerHoldingsSnapshot(
                available=False,
                manager_id=manager_id,
                error="robinhood_credentials_missing",
            )
        broker = brokers_by_manager.get(manager_id)
        if not isinstance(broker, RobinhoodBroker):
            return BrokerHoldingsSnapshot(
                available=False,
                manager_id=manager_id,
                error="broker_not_robinhood",
            )

        holdings, metrics, holdings_error = await asyncio.to_thread(broker.get_broker_snapshot)
        if holdings_error:
            return BrokerHoldingsSnapshot(
                available=False,
                account_number=broker._account_number,
                manager_id=manager_id,
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
            manager_id=manager_id,
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
        manager: str | None = Query(default=None),
    ) -> PortfolioChartResponse:
        range_key = range_key if range_key in portfolio_history.RANGE_KEYS else "1w"
        manager_id = _resolve_manager_id(manager)
        current_value: float | None = None
        account_number: str | None = None

        broker = brokers_by_manager.get(manager_id)
        if isinstance(broker, RobinhoodBroker):
            broker_holdings = await _fetch_broker_holdings(
                manager_id=manager_id,
                record_snapshot=False,
            )
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
                manager_id=manager_id,
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
        ticker: str | None = Query(default=None),
        traded_filter: str = Query(default=DEFAULT_TWEET_TRADED_FILTER, alias="traded"),
        sort: str = Query(default=DEFAULT_TWEET_SORT),
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
                ticker=normalize_ticker_prefix(ticker),
                traded_filter=normalize_traded_filter(traded_filter),
                sort=normalize_tweet_sort(sort),
            )
            feedback = _feedback_map(db)
        return [_tweet_to_dashboard_read(row, feedback) for row in rows]

    @router.get("/dashboard/trades", response_model=list[TradeRead])
    async def dashboard_trades(
        range_key: str = Query(default=DEFAULT_TWEET_RANGE, alias="range"),
        since: datetime | None = Query(default=None),
        until: datetime | None = Query(default=None),
        limit: int = Query(default=DEFAULT_TRADE_LIMIT, ge=1, le=MAX_TRADE_LIMIT),
        ticker: str | None = Query(default=None),
        action_filter: str = Query(default=DEFAULT_TRADE_ACTION_FILTER, alias="action"),
        status_filter: str = Query(default=DEFAULT_TRADE_STATUS_FILTER, alias="status"),
        mode_filter: str = Query(default=DEFAULT_TRADE_MODE_FILTER, alias="mode"),
        sort: str = Query(default=DEFAULT_TRADE_SORT),
        manager: str | None = Query(default=None),
    ) -> list[TradeRead]:
        try:
            since_dt, until_dt = resolve_tweet_window(
                range_key=range_key,
                since=since,
                until=until,
                now=datetime.now(timezone.utc),
            )
        except TweetWindowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        manager_id = _resolve_manager_id(manager)
        with session_factory() as db:
            rows = fetch_dashboard_trades(
                db,
                manager_id=manager_id,
                since=since_dt,
                until=until_dt,
                limit=limit,
                ticker=normalize_ticker_prefix(ticker),
                action_filter=normalize_trade_action_filter(action_filter),
                status_filter=normalize_trade_status_filter(status_filter),
                mode_filter=normalize_trade_mode_filter(mode_filter),
                sort=normalize_trade_sort(sort),
            )
        return [TradeRead.model_validate(row) for row in rows]

    @router.get("/dashboard/data", response_model=DashboardSnapshot)
    async def dashboard_data(
        live_only: bool = Query(default=False),
        include_broker: bool = Query(default=True),
        fetch_live_pnl_prices: bool | None = Query(default=None),
        manager: str | None = Query(default=None),
    ) -> DashboardSnapshot:
        if pnl_service is None:
            raise HTTPException(status_code=503, detail="pnl_service_unavailable")

        manager_id = _resolve_manager_id(manager)
        snapshot = orchestrator.snapshot()
        use_live_pnl_prices = (
            fetch_live_pnl_prices if fetch_live_pnl_prices is not None else include_broker
        )
        include_simulation = settings.pnl_include_simulation and not live_only
        original_include = pnl_service.include_simulation
        pnl_service.include_simulation = include_simulation
        try:
            pnl = pnl_service.build_report(
                fetch_live_prices=use_live_pnl_prices,
                manager_id=manager_id,
            )
        finally:
            pnl_service.include_simulation = original_include

        with session_factory() as db:
            trades = db.execute(
                select(Trade)
                .where(Trade.manager_id == manager_id)
                .order_by(Trade.created_at.desc())
                .limit(15)
            ).scalars().all()
            recognized = db.execute(
                select(RecognizedTicker.ticker)
                .where(RecognizedTicker.manager_id == manager_id)
                .order_by(RecognizedTicker.ticker.asc())
            ).scalars().all()
            watchlist_rows = db.execute(
                select(WatchlistEntry)
                .where(WatchlistEntry.manager_id == manager_id)
                .order_by(WatchlistEntry.conviction_score.desc(), WatchlistEntry.last_seen_at.desc())
            ).scalars().all()

        if include_broker:
            broker_holdings = await _fetch_broker_holdings(manager_id=manager_id)
        else:
            broker_holdings = BrokerHoldingsSnapshot(
                available=False,
                manager_id=manager_id,
                error="positions_refresh_skipped",
            )

        digest_row = digest_service.get()
        daily_digest = DailyDigestRead(**digest_service.to_api_dict(digest_row)) if digest_row else None
        return DashboardSnapshot(
            health=_health_response(active_manager=manager_id, snapshot=snapshot),
            pnl=pnl,
            broker_holdings=broker_holdings,
            recent_tweets=[],
            recent_trades=[TradeRead.model_validate(row) for row in trades],
            recognized_tickers=[str(ticker) for ticker in recognized],
            watchlist=[WatchlistEntryRead.model_validate(row) for row in watchlist_rows],
            worker_iteration_count=snapshot.iteration_count,
            worker_last_error=snapshot.last_error,
            active_manager=manager_id,
            managers=snapshot.managers,
            daily_digest=daily_digest,
        )

    @router.get("/parser/feedback", response_model=list[ParserFeedbackRead])
    async def list_parser_feedback(limit: int = Query(default=50, ge=1, le=200)) -> list[ParserFeedbackRead]:
        with session_factory() as db:
            rows = db.execute(
                select(ParserFeedback).order_by(ParserFeedback.created_at.desc()).limit(limit)
            ).scalars().all()
        return [ParserFeedbackRead.model_validate(row) for row in rows]

    @router.get("/dashboard/review-queue", response_model=list[ReviewQueueItem])
    async def dashboard_review_queue(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[ReviewQueueItem]:
        with session_factory() as db:
            signals = (
                db.execute(
                    select(ParsedSignal)
                    .options(selectinload(ParsedSignal.tweet))
                    .where(ParsedSignal.needs_review.is_(True))
                    .order_by(ParsedSignal.created_at.desc())
                    .limit(limit * 3)
                )
                .scalars()
                .all()
            )
            items: list[ReviewQueueItem] = []
            for signal in signals:
                label_stmt = select(TweetLabel).where(TweetLabel.tweet_id == signal.source_tweet_id)
                if signal.ticker:
                    label_stmt = label_stmt.where(TweetLabel.ticker == signal.ticker)
                existing_label = db.execute(label_stmt.limit(1)).scalar_one_or_none()
                if existing_label is not None:
                    continue
                tweet = signal.tweet
                items.append(
                    ReviewQueueItem(
                        signal_id=signal.id,
                        tweet_id=signal.source_tweet_id,
                        tweet_text=tweet.text if tweet is not None else signal.raw_text,
                        ticker=signal.ticker,
                        action=signal.action,
                        confidence=signal.confidence,
                        review_reason=signal.review_reason,
                        posted_at=tweet.posted_at if tweet is not None else None,
                        created_at=signal.created_at,
                        manager_id=signal.manager_id,
                    )
                )
                if len(items) >= limit:
                    break
        return items

    @router.post(
        "/dashboard/review-queue/{signal_id}/label",
        response_model=ReviewQueueLabelRead,
    )
    async def label_review_queue_item(
        signal_id: int,
        body: ReviewQueueLabelCreate,
    ) -> ReviewQueueLabelRead:
        if body.action not in {SignalAction.BUY, SignalAction.SELL}:
            raise HTTPException(status_code=422, detail="action_must_be_buy_or_sell")
        with session_factory() as db:
            signal = db.execute(
                select(ParsedSignal)
                .options(selectinload(ParsedSignal.tweet))
                .where(ParsedSignal.id == signal_id)
            ).scalar_one_or_none()
            if signal is None:
                raise HTTPException(status_code=404, detail="signal_not_found")
            tweet_text = signal.tweet.text if signal.tweet is not None else signal.raw_text
            label = TweetLabel(
                tweet_id=signal.source_tweet_id,
                ticker=signal.ticker,
                action=body.action,
                segment_text=signal.raw_text or tweet_text,
                labeled_by=body.labeled_by,
            )
            db.add(label)
            signal.needs_review = False
            signal.review_reason = None
            db.commit()
            db.refresh(label)
            return ReviewQueueLabelRead(
                signal_id=signal.id,
                tweet_id=signal.source_tweet_id,
                ticker=signal.ticker,
                action=body.action,
                needs_review=signal.needs_review,
                label_id=label.id,
            )

    @router.delete("/dashboard/review-queue/{signal_id}/label")
    async def undo_review_queue_label(signal_id: int) -> dict:
        with session_factory() as db:
            signal = db.execute(
                select(ParsedSignal).where(ParsedSignal.id == signal_id)
            ).scalar_one_or_none()
            if signal is None:
                raise HTTPException(status_code=404, detail="signal_not_found")
            label_stmt = select(TweetLabel).where(TweetLabel.tweet_id == signal.source_tweet_id)
            if signal.ticker:
                label_stmt = label_stmt.where(TweetLabel.ticker == signal.ticker)
            labels = db.execute(label_stmt.order_by(TweetLabel.created_at.desc())).scalars().all()
            if not labels:
                raise HTTPException(status_code=404, detail="label_not_found")
            for label in labels:
                db.delete(label)
            signal.needs_review = True
            signal.review_reason = signal.review_reason or "trade_header_low_conf"
            db.commit()
        return {"ok": True, "signal_id": signal_id}

    @router.post("/parser/feedback", response_model=ParserFeedbackRead)
    async def create_parser_feedback(body: ParserFeedbackCreate) -> ParserFeedbackRead:
        with session_factory() as db:
            tweet = db.execute(
                select(Tweet)
                .options(selectinload(Tweet.parsed_signals))
                .where(Tweet.tweet_id == body.tweet_id)
            ).scalar_one_or_none()
            if tweet is None:
                raise HTTPException(status_code=404, detail="tweet_not_found")
            latest = None
            if tweet.parsed_signals:
                latest = max(tweet.parsed_signals, key=lambda signal: signal.created_at)
            existing = db.execute(
                select(ParserFeedback).where(ParserFeedback.tweet_id == body.tweet_id)
            ).scalar_one_or_none()
            if existing is None:
                existing = ParserFeedback(tweet_id=body.tweet_id, tweet_text=tweet.text)
                db.add(existing)
            existing.tweet_text = tweet.text
            existing.parser_action = latest.action if latest else SignalAction.IGNORE
            existing.parser_ticker = latest.ticker if latest else None
            existing.correct_action = body.correct_action
            existing.note = body.note
            existing.exported_at = None
            db.commit()
            db.refresh(existing)
            return ParserFeedbackRead.model_validate(existing)

    @router.delete("/parser/feedback/{tweet_id}")
    async def delete_parser_feedback(tweet_id: str) -> dict:
        with session_factory() as db:
            row = db.execute(
                select(ParserFeedback).where(ParserFeedback.tweet_id == tweet_id)
            ).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="feedback_not_found")
            db.delete(row)
            db.commit()
        return {"ok": True, "tweet_id": tweet_id}

    @router.get("/digest/latest", response_model=DailyDigestRead)
    async def digest_latest() -> DailyDigestRead:
        row = digest_service.get()
        if row is None:
            row = digest_service.rebuild()
        if row is None:
            raise HTTPException(status_code=404, detail="digest_unavailable")
        return DailyDigestRead(**digest_service.to_api_dict(row))

    @router.get("/digest", response_model=DailyDigestRead)
    async def digest_by_date(date: str | None = Query(default=None)) -> DailyDigestRead:
        row = digest_service.get(date)
        if row is None:
            row = digest_service.rebuild(date)
        if row is None:
            raise HTTPException(status_code=404, detail="digest_not_found")
        return DailyDigestRead(**digest_service.to_api_dict(row))

    @router.post("/digest/rebuild", response_model=DailyDigestRead)
    async def digest_rebuild(date: str | None = Query(default=None)) -> DailyDigestRead:
        row = digest_service.rebuild(date, force=True)
        if row is None:
            raise HTTPException(status_code=404, detail="digest_rebuild_failed")
        return DailyDigestRead(**digest_service.to_api_dict(row))

    @router.post("/digest/finalize", response_model=DailyDigestRead)
    async def digest_finalize(date: str | None = Query(default=None)) -> DailyDigestRead:
        row = digest_service.finalize(date)
        if row is None:
            raise HTTPException(status_code=404, detail="digest_finalize_failed")
        return DailyDigestRead(**digest_service.to_api_dict(row))

    @router.get("/broker/holdings", response_model=BrokerHoldingsSnapshot)
    async def broker_holdings(manager: str | None = Query(default=None)) -> BrokerHoldingsSnapshot:
        return await _fetch_broker_holdings(manager_id=_resolve_manager_id(manager))

    @router.get("/dashboard")
    async def dashboard() -> FileResponse:
        html_path = Path(__file__).resolve().parent / "dashboard.html"
        return FileResponse(html_path)

    @router.get("/dashboard/balance-chart.js")
    async def balance_chart_js() -> FileResponse:
        js_path = Path(__file__).resolve().parent / "balance-chart.js"
        return FileResponse(js_path, media_type="application/javascript")

    @router.post("/pause", response_model=WorkerControlResponse)
    async def pause_worker(manager: str | None = Query(default=None)) -> WorkerControlResponse:
        orchestrator.pause(manager)
        snapshot = orchestrator.snapshot()
        label = manager or "all"
        return WorkerControlResponse(
            running=snapshot.running,
            paused=snapshot.paused,
            message=f"worker paused ({label})",
        )

    @router.post("/resume", response_model=WorkerControlResponse)
    async def resume_worker(manager: str | None = Query(default=None)) -> WorkerControlResponse:
        orchestrator.resume(manager)
        snapshot = orchestrator.snapshot()
        label = manager or "all"
        return WorkerControlResponse(
            running=snapshot.running,
            paused=snapshot.paused,
            message=f"worker resumed ({label})",
        )

    return router
