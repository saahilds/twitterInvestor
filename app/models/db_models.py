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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    tweet: Mapped[Tweet] = relationship(back_populates="parsed_signals")
    trades: Mapped[list["Trade"]] = relationship(back_populates="parsed_signal")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parsed_signal_id: Mapped[int] = mapped_column(ForeignKey("parsed_signals.id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[SignalAction] = mapped_column(Enum(SignalAction), index=True)
    amount_usd: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    simulation: Mapped[bool] = mapped_column(Boolean, default=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    parsed_signal: Mapped[ParsedSignal] = relationship(back_populates="trades")


class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
