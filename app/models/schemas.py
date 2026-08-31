from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.db_models import SignalAction
from app.parsing.buy_conviction import BuyConviction
from app.parsing.watch_conviction import WatchConviction


class IngestedTweet(BaseModel):
    tweet_pk: int
    tweet_id: str
    account: str
    text: str
    posted_at: datetime
    fetched_at: datetime
    is_reply: bool
    is_retweet: bool


class TradeSignal(BaseModel):
    source_tweet_id: str
    ticker: str | None = None
    action: SignalAction = SignalAction.IGNORE
    confidence: float = 0.0
    strength: str = "none"
    score: int = 0
    raw_text: str
    suggested_trade_usd: float = 0.0
    portfolio_allocation_pct: float | None = None
    sell_fraction: float | None = None
    target_portfolio_pct: float | None = None
    sell_sizing_explicit: bool = False
    buy_conviction: BuyConviction | None = None
    watch_conviction: WatchConviction | None = None
    needs_review: bool = False
    review_reason: str | None = None


class RiskCheckResult(BaseModel):
    allowed: bool
    reason: str
    normalized_trade_usd: float | None = None
    is_new_ticker: bool = False
    sell_fraction: float | None = None
    sell_quantity: float | None = None


class BrokerOrderResult(BaseModel):
    status: str
    order_id: str | None = None
    simulation: bool = True
    quantity: float | None = None
    order_type: str | None = None
    ask_price: float | None = None
    limit_price: float | None = None
    fill_price: float | None = None
    error_message: str | None = None
    account_number: str | None = None
    raw_response: dict | None = None


class TweetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tweet_id: str
    account: str
    text: str
    posted_at: datetime
    fetched_at: datetime
    is_reply: bool
    is_retweet: bool
    url: str | None


class ParsedSignalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_tweet_id: str
    ticker: str | None
    action: SignalAction
    confidence: float
    strength: str
    score: int
    suggested_trade_usd: float
    rejection_reason: str | None
    watch_conviction: str | None = None
    target_portfolio_pct: float | None = None
    manager_id: str
    created_at: datetime


class TradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parsed_signal_id: int
    source_tweet_id: str | None
    ticker: str
    action: SignalAction
    amount_usd: float
    quantity: float | None
    status: str
    simulation: bool
    broker_order_id: str | None
    order_type: str | None
    ask_price: float | None
    limit_price: float | None
    fill_price: float | None
    error_message: str | None
    account_number: str | None
    manager_id: str
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
    worker_running: bool
    worker_paused: bool
    simulation_mode: bool
    live_trading_enabled: bool
    order_execution_mode: str
    trading_window_enabled: bool
    within_market_hours: bool
    target_account: str
    poll_interval_seconds: int = 60
    dashboard_positions_refresh_seconds: int = 300
    default_buy_allocation_pct: float = 1.0
    robinhood_logged_in: bool | None = None
    robinhood_auth_error: str | None = None
    robinhood_auth_retry_in_seconds: int | None = None
    robinhood_auth_last_at: datetime | None = None
    robinhood_auth_age_days: float | None = None
    robinhood_auth_days_remaining: float | None = None
    robinhood_auth_refresh_due_at: datetime | None = None
    robinhood_auth_status: str | None = None
    robinhood_reauth_status: str | None = None
    managers: list[ManagerStateSnapshot] = Field(default_factory=list)
    active_manager: str | None = None


class RobinhoodReauthStatusResponse(BaseModel):
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    message: str | None = None


class DashboardTweetRead(TweetRead):
    signal_action: str | None = None
    signal_ticker: str | None = None
    signal_confidence: float | None = None
    signal_rejection_reason: str | None = None
    buy_conviction: str | None = None
    traded: bool = False
    trade_status: str | None = None
    trade_amount_usd: float | None = None
    feedback_correct_action: str | None = None


class ParserFeedbackCreate(BaseModel):
    tweet_id: str
    correct_action: SignalAction
    note: str | None = None


class ParserFeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tweet_id: str
    tweet_text: str
    parser_action: SignalAction
    parser_ticker: str | None
    correct_action: SignalAction
    note: str | None
    created_at: datetime
    exported_at: datetime | None


class ReviewQueueItem(BaseModel):
    signal_id: int
    tweet_id: str
    tweet_text: str
    ticker: str | None
    action: SignalAction
    confidence: float
    review_reason: str | None
    posted_at: datetime | None
    created_at: datetime
    manager_id: str


class ReviewQueueLabelCreate(BaseModel):
    action: SignalAction
    labeled_by: str = "dashboard"


class ReviewQueueLabelRead(BaseModel):
    signal_id: int
    tweet_id: str
    ticker: str | None
    action: SignalAction
    needs_review: bool
    label_id: int


class DailyDigestRead(BaseModel):
    digest_date: str
    status: str
    current_period: str
    summary_markdown: str = ""
    periods: dict = Field(default_factory=dict)
    trades_day: list[str] = Field(default_factory=list)
    rejected_day: list[str] = Field(default_factory=list)
    informational_day: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    last_rebuilt_at: str | None = None
    completed_at: str | None = None


class WorkerControlResponse(BaseModel):
    running: bool
    paused: bool
    message: str


class WorkerStateSnapshot(BaseModel):
    running: bool
    paused: bool
    iteration_count: int = Field(default=0)
    last_error: str | None = None


class ManagerStateSnapshot(BaseModel):
    manager_id: str
    account_number: str | None = None
    paused: bool = False
    enabled: bool = True


class OrchestratorStateSnapshot(BaseModel):
    running: bool
    paused: bool
    iteration_count: int = Field(default=0)
    last_error: str | None = None
    managers: list[ManagerStateSnapshot] = Field(default_factory=list)


class TickerPnlRead(BaseModel):
    ticker: str
    shares_held: float
    avg_cost_basis: float
    cost_basis_open: float
    last_price: float | None
    market_value: float | None
    realized_pnl: float
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    total_pnl: float
    buy_count: int
    sell_count: int


class PortfolioPnlResponse(BaseModel):
    tickers: list[TickerPnlRead]
    realized_pnl_total: float
    unrealized_pnl_total: float
    total_pnl: float
    include_simulation: bool
    prices_as_of: str
    manager_id: str | None = None


class RobinhoodHoldingRead(BaseModel):
    ticker: str
    quantity: float
    average_cost: float
    last_price: float | None
    market_value: float | None
    cost_basis: float
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


class BrokerHoldingsSnapshot(BaseModel):
    available: bool
    account_number: str | None = None
    manager_id: str | None = None
    error: str | None = None
    holdings: list[RobinhoodHoldingRead] = Field(default_factory=list)
    portfolio_equity: float | None = None
    holdings_market_value: float | None = None
    positions_market_value: float | None = None
    profile_market_value: float | None = None
    stocks_plus_cash: float | None = None
    cash: float | None = None
    total_market_value: float | None = None
    total_unrealized_pnl: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def account_balance(self) -> float | None:
        """Total account value (equities + cash) from Robinhood."""
        return self.stocks_plus_cash or self.portfolio_equity or self.total_market_value


class ChartPointRead(BaseModel):
    t: str
    v: float  # stocks_plus_cash (account balance)


class TradeChartAnnotationRead(BaseModel):
    trade_id: int
    t: str
    ticker: str
    action: str
    amount_usd: float
    status: str
    simulation: bool
    label: str


class PortfolioChartSummary(BaseModel):
    current_value: float
    period_start_value: float
    change_usd: float
    change_pct: float


class PortfolioChartResponse(BaseModel):
    range: str
    source: str
    window_start: str | None = None
    window_end: str | None = None
    summary: PortfolioChartSummary | None = None
    points: list[ChartPointRead]
    annotations: list[TradeChartAnnotationRead]
    session_open: str | None = None
    session_end: str | None = None


class WatchlistEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    manager_id: str
    ticker: str
    conviction_score: float
    last_seen_at: datetime
    source_tweet_id: str | None
    watch_conviction: str


class DashboardSnapshot(BaseModel):
    health: HealthResponse
    pnl: PortfolioPnlResponse
    broker_holdings: BrokerHoldingsSnapshot
    recent_tweets: list[DashboardTweetRead]
    recent_trades: list[TradeRead]
    recognized_tickers: list[str]
    watchlist: list[WatchlistEntryRead] = Field(default_factory=list)
    worker_iteration_count: int
    worker_last_error: str | None = None
    active_manager: str | None = None
    managers: list[ManagerStateSnapshot] = Field(default_factory=list)
    daily_digest: DailyDigestRead | None = None
