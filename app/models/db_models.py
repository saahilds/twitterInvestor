from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignalAction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    WATCH = "WATCH"
    IGNORE = "IGNORE"


class Tweet(Base):
    __tablename__ = "tweets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tweet_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    is_retweet: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    parsed_signals: Mapped[list["ParsedSignal"]] = relationship(back_populates="tweet")


class RecognizedTicker(Base):
    __tablename__ = "recognized_tickers"

    manager_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    source_tweet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    manager_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    conviction_score: Mapped[float] = mapped_column(Float, default=0.0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    source_tweet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    watch_conviction: Mapped[str] = mapped_column(String(32), default="watch")


class ParsedSignal(Base):
    __tablename__ = "parsed_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tweet_pk: Mapped[int] = mapped_column(ForeignKey("tweets.id"), index=True)
    source_tweet_id: Mapped[str] = mapped_column(String(64), index=True)
    ticker: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    action: Mapped[SignalAction] = mapped_column(Enum(SignalAction), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    strength: Mapped[str] = mapped_column(String(32), default="none")
    score: Mapped[int] = mapped_column(Integer, default=0)
    raw_text: Mapped[str] = mapped_column(Text)
    suggested_trade_usd: Mapped[float] = mapped_column(Float, default=0.0)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    watch_conviction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    manager_id: Mapped[str] = mapped_column(String(32), index=True, default="individual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    tweet: Mapped[Tweet] = relationship(back_populates="parsed_signals")
    trades: Mapped[list["Trade"]] = relationship(back_populates="parsed_signal")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parsed_signal_id: Mapped[int] = mapped_column(ForeignKey("parsed_signals.id"), index=True)
    source_tweet_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[SignalAction] = mapped_column(Enum(SignalAction), index=True)
    amount_usd: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    simulation: Mapped[bool] = mapped_column(Boolean, default=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    order_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ask_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manager_id: Mapped[str] = mapped_column(String(32), index=True, default="individual")
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    parsed_signal: Mapped[ParsedSignal] = relationship(back_populates="trades")


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    stocks_plus_cash: Mapped[float] = mapped_column(Float)
    holdings_market_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash: Mapped[float | None] = mapped_column(Float, nullable=True)


class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
