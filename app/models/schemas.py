from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.db_models import SignalAction


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


class RiskCheckResult(BaseModel):
    allowed: bool
    reason: str
    normalized_trade_usd: float | None = None
    is_new_ticker: bool = False


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
    default_trade_size_usd: float = 1.0
    robinhood_logged_in: bool | None = None
    robinhood_auth_error: str | None = None
    robinhood_auth_retry_in_seconds: int | None = None


class DashboardTweetRead(TweetRead):
    signal_action: str | None = None
    signal_ticker: str | None = None
    signal_confidence: float | None = None
    signal_rejection_reason: str | None = None


class WorkerControlResponse(BaseModel):
    running: bool
    paused: bool
    message: str


class WorkerStateSnapshot(BaseModel):
    running: bool
    paused: bool
    iteration_count: int = Field(default=0)
    last_error: str | None = None


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


class PortfolioChartResponse(BaseModel):
    range: str
    source: str
    points: list[ChartPointRead]
    annotations: list[TradeChartAnnotationRead]
    session_open: str | None = None
    session_end: str | None = None


class DashboardSnapshot(BaseModel):
    health: HealthResponse
    pnl: PortfolioPnlResponse
    broker_holdings: BrokerHoldingsSnapshot
    recent_tweets: list[DashboardTweetRead]
    recent_trades: list[TradeRead]
    recognized_tickers: list[str]
    worker_iteration_count: int
    worker_last_error: str | None = None
