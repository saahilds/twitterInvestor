from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.models.schemas import RiskCheckResult, TradeSignal


@dataclass(slots=True)
class RiskConfig:
    allowlist: set[str]
    max_trade_size_usd: float
    default_trade_size_usd: float
    cooldown_seconds: int
    duplicate_window_seconds: int


class RiskManager:
    """Minimal risk checks for Phase 1 safety."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def evaluate(self, signal: TradeSignal, db: Session) -> RiskCheckResult:
        if signal.action == SignalAction.IGNORE:
            return RiskCheckResult(allowed=False, reason="parser_action_ignore")

        if not signal.ticker:
            return RiskCheckResult(allowed=False, reason="missing_ticker")

        ticker = signal.ticker.upper()
        if ticker not in self.config.allowlist:
            return RiskCheckResult(allowed=False, reason=f"ticker_not_allowed:{ticker}")

        normalized_trade = max(
            0.0,
            min(
                signal.suggested_trade_usd or self.config.default_trade_size_usd,
                self.config.max_trade_size_usd,
            ),
        )
        if normalized_trade <= 0:
            return RiskCheckResult(allowed=False, reason="invalid_trade_size")

        now = datetime.now(timezone.utc)

        cooldown_cutoff = now - timedelta(seconds=self.config.cooldown_seconds)
        recent_trade = db.execute(
            select(Trade)
            .where(and_(Trade.ticker == ticker, Trade.created_at >= cooldown_cutoff))
            .order_by(Trade.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if recent_trade is not None:
            return RiskCheckResult(allowed=False, reason=f"cooldown_active:{ticker}")

        duplicate_cutoff = now - timedelta(seconds=self.config.duplicate_window_seconds)
        duplicate_signal = db.execute(
            select(ParsedSignal)
            .where(
                and_(
                    ParsedSignal.ticker == ticker,
                    ParsedSignal.action == signal.action,
                    ParsedSignal.created_at >= duplicate_cutoff,
                )
            )
            .order_by(ParsedSignal.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if duplicate_signal is not None:
            return RiskCheckResult(allowed=False, reason=f"duplicate_signal:{ticker}:{signal.action.value}")

        return RiskCheckResult(
            allowed=True,
            reason="ok",
            normalized_trade_usd=round(normalized_trade, 2),
        )
