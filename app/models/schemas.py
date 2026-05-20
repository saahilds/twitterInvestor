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


class BrokerOrderResult(BaseModel):
    status: str
    order_id: str | None = None
    simulation: bool = True
    quantity: float | None = None
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
    ticker: str
    action: SignalAction
    amount_usd: float
    quantity: float | None
    status: str
    simulation: bool
    broker_order_id: str | None
    created_at: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
    worker_running: bool
    worker_paused: bool
    simulation_mode: bool
    target_account: str


class WorkerControlResponse(BaseModel):
    running: bool
    paused: bool
    message: str


class WorkerStateSnapshot(BaseModel):
    running: bool
    paused: bool
    iteration_count: int = Field(default=0)
    last_error: str | None = None
