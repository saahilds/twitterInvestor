from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.models.schemas import RiskCheckResult, TradeSignal
from app.risk.market_hours import is_within_regular_market_hours, us_trading_day_start_utc


@dataclass(slots=True)
class RiskConfig:
    allowlist: set[str]
    max_trade_size_usd: float
    default_trade_size_usd: float
    cooldown_seconds: int
    duplicate_window_seconds: int
    trading_window_enabled: bool = True
    us_symbols_only: bool = True
    max_trades_per_ticker_per_day: int = 1
    daily_limit_counts_simulation: bool = False


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

        if self.config.us_symbols_only and not _is_us_symbol(ticker):
            return RiskCheckResult(allowed=False, reason=f"non_us_symbol:{ticker}")

        if self.config.trading_window_enabled and not is_within_regular_market_hours():
            return RiskCheckResult(allowed=False, reason="outside_market_hours")

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

        if self._tweet_already_traded(signal.source_tweet_id, db):
            return RiskCheckResult(allowed=False, reason=f"duplicate_tweet:{signal.source_tweet_id}")

        if self._daily_ticker_limit_reached(ticker, db):
            return RiskCheckResult(allowed=False, reason=f"daily_limit:{ticker}")

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

    def _tweet_already_traded(self, source_tweet_id: str, db: Session) -> bool:
        existing = db.execute(
            select(Trade.id)
            .join(ParsedSignal, Trade.parsed_signal_id == ParsedSignal.id)
            .where(ParsedSignal.source_tweet_id == source_tweet_id)
            .limit(1)
        ).scalar_one_or_none()
        return existing is not None

    def _daily_ticker_limit_reached(self, ticker: str, db: Session) -> bool:
        if self.config.max_trades_per_ticker_per_day <= 0:
            return False

        day_start = us_trading_day_start_utc()
        query = select(Trade.id).where(
            and_(Trade.ticker == ticker, Trade.created_at >= day_start)
        )
        if not self.config.daily_limit_counts_simulation:
            query = query.where(Trade.simulation.is_(False))

        trades_today = db.execute(query).all()
        return len(trades_today) >= self.config.max_trades_per_ticker_per_day


def _is_us_symbol(ticker: str) -> bool:
    return "." not in ticker
