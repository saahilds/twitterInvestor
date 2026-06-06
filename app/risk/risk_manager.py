from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.execution.holdings import BrokerHolding
from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.models.schemas import RiskCheckResult, TradeSignal
from app.risk.market_hours import is_within_regular_market_hours, us_trading_day_start_utc
from app.risk.sell_sizing import resolve_sell_notional_usd
from app.services.recognized_tickers import RecognizedTickerRegistry


@dataclass(slots=True)
class RiskConfig:
    seed_tickers: set[str]
    max_trade_size_usd: float
    default_trade_size_usd: float
    new_ticker_size_multiplier: float
    cooldown_seconds: int
    duplicate_window_seconds: int
    trading_window_enabled: bool = True
    us_symbols_only: bool = True
    max_trades_per_ticker_per_day: int = 1
    daily_limit_counts_simulation: bool = False
    live_trading_enabled: bool = False
    min_buy_confidence_unlisted: float = 0.0
    min_sell_notional_usd: float = 1.0


class RiskManager:
    """Minimal risk checks for Phase 1 safety."""

    def __init__(
        self,
        config: RiskConfig,
        registry: RecognizedTickerRegistry | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or RecognizedTickerRegistry()

    def evaluate(
        self,
        signal: TradeSignal,
        db: Session,
        *,
        cash_available_usd: float | None = None,
        holding: BrokerHolding | None = None,
    ) -> RiskCheckResult:
        if signal.action == SignalAction.IGNORE:
            return RiskCheckResult(allowed=False, reason="parser_action_ignore")

        if not signal.ticker:
            return RiskCheckResult(allowed=False, reason="missing_ticker")

        ticker = signal.ticker.upper()
        recognized = ticker in self.config.seed_tickers or self.registry.is_recognized(ticker, db)

        if (
            signal.action == SignalAction.BUY
            and not recognized
            and signal.confidence < self.config.min_buy_confidence_unlisted
        ):
            return RiskCheckResult(allowed=False, reason="unlisted_buy_low_confidence")

        if self.config.us_symbols_only and not _is_us_symbol(ticker):
            return RiskCheckResult(allowed=False, reason=f"non_us_symbol:{ticker}")

        if self.config.trading_window_enabled and not is_within_regular_market_hours():
            return RiskCheckResult(allowed=False, reason="outside_market_hours")

        sell_fraction: float | None = None
        if signal.action == SignalAction.SELL:
            if holding is None or holding.quantity <= 0:
                return RiskCheckResult(allowed=False, reason=f"not_in_portfolio:{ticker}")
            sell_fraction = signal.sell_fraction if signal.sell_fraction is not None else 1.0
            if sell_fraction <= 0:
                return RiskCheckResult(allowed=False, reason="sell_fraction_zero")
            normalized_trade = resolve_sell_notional_usd(
                holding,
                sell_fraction,
                max_trade_size_usd=self.config.max_trade_size_usd,
                min_trade_notional_usd=self.config.min_sell_notional_usd,
            )
            if normalized_trade is None or normalized_trade <= 0:
                return RiskCheckResult(allowed=False, reason="invalid_sell_size")
            is_new_ticker = False
        else:
            normalized_trade, is_new_ticker = self._resolve_trade_size(
                signal=signal,
                recognized=recognized,
                cash_available_usd=cash_available_usd,
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

        if signal.action == SignalAction.SELL:
            pct = int(round((sell_fraction or 0) * 100))
            reason = f"sell_{pct}pct_portfolio"
        else:
            reason = "new_ticker_sized_up" if is_new_ticker else "ok"
        return RiskCheckResult(
            allowed=True,
            reason=reason,
            normalized_trade_usd=round(normalized_trade, 2),
            is_new_ticker=is_new_ticker,
            sell_fraction=sell_fraction,
        )

    def _resolve_trade_size(
        self,
        *,
        signal: TradeSignal,
        recognized: bool,
        cash_available_usd: float | None,
    ) -> tuple[float, bool]:
        if signal.action != SignalAction.BUY:
            return 0.0, False

        if recognized:
            size = min(self.config.default_trade_size_usd, self.config.max_trade_size_usd)
            return max(0.0, size), False

        target = self.config.default_trade_size_usd * self.config.new_ticker_size_multiplier
        if cash_available_usd is not None:
            target = min(target, cash_available_usd)
        return max(0.0, target), True

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
