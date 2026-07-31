from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.execution.holdings import BrokerHolding
from app.models.db_models import ParsedSignal, SignalAction, Trade
from app.models.schemas import RiskCheckResult, TradeSignal
from app.parsing.buy_conviction import BuyConviction
from app.risk.market_hours import is_within_regular_market_hours, us_trading_day_start_utc
from app.risk.portfolio_sizing import (
    resolve_buy_allocation_pct,
    resolve_buy_notional_usd,
    resolve_max_sell_notional_usd,
    resolve_min_trade_notional_usd,
    resolve_portfolio_value,
)
from app.risk.sell_sizing import resolve_sell_order
from app.services.recognized_tickers import RecognizedTickerRegistry
from app.services.watchlist import WatchlistRegistry


@dataclass(slots=True)
class RiskConfig:
    seed_tickers: set[str]
    default_buy_allocation_pct: float
    max_buy_allocation_pct: float
    standard_buy_allocation_pct_max: float
    reload_buy_allocation_pct_max: float
    thesis_buy_allocation_pct_min: float
    thesis_buy_allocation_pct_max: float
    min_trade_notional_pct: float
    min_trade_notional_usd: float
    cash_buffer_pct: float
    max_sell_notional_pct: float
    simulation_portfolio_usd: float
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
    watchlist_stale_days: int = 30
    watchlist_max_conviction_score: float = 5.0


class RiskManager:
    """Minimal risk checks for Phase 1 safety."""

    def __init__(
        self,
        config: RiskConfig,
        registry: RecognizedTickerRegistry | None = None,
        watchlist: WatchlistRegistry | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or RecognizedTickerRegistry()
        self.watchlist = watchlist or WatchlistRegistry(
            max_conviction_score=config.watchlist_max_conviction_score,
            stale_days=config.watchlist_stale_days,
        )

    def evaluate(
        self,
        signal: TradeSignal,
        db: Session,
        *,
        manager_id: str,
        cash_available_usd: float | None = None,
        portfolio_value_usd: float | None = None,
        holding: BrokerHolding | None = None,
        as_of: datetime | None = None,
    ) -> RiskCheckResult:
        if signal.action == SignalAction.IGNORE:
            return RiskCheckResult(allowed=False, reason="parser_action_ignore")

        if signal.action == SignalAction.WATCH:
            return RiskCheckResult(allowed=False, reason="watch_signal_no_trade")

        if not signal.ticker:
            return RiskCheckResult(allowed=False, reason="missing_ticker")

        ticker = signal.ticker.upper()
        recognized = ticker in self.config.seed_tickers or self.registry.is_recognized(
            ticker, db, manager_id=manager_id
        )

        if (
            signal.action == SignalAction.BUY
            and not recognized
            and signal.confidence < self.config.min_buy_confidence_unlisted
        ):
            return RiskCheckResult(allowed=False, reason="unlisted_buy_low_confidence")

        if self.config.us_symbols_only and not _is_us_symbol(ticker):
            return RiskCheckResult(allowed=False, reason=f"non_us_symbol:{ticker}")

        if self.config.trading_window_enabled and not is_within_regular_market_hours(as_of):
            return RiskCheckResult(allowed=False, reason="outside_market_hours")

        portfolio = resolve_portfolio_value(
            portfolio_value_usd=portfolio_value_usd,
            simulation_portfolio_usd=self.config.simulation_portfolio_usd,
        )
        min_notional = resolve_min_trade_notional_usd(
            portfolio,
            min_trade_notional_pct=self.config.min_trade_notional_pct,
            min_trade_notional_usd=self.config.min_trade_notional_usd,
        )

        sell_fraction: float | None = None
        sell_quantity: float | None = None
        buy_conviction: BuyConviction | None = None
        if signal.action == SignalAction.SELL:
            if holding is None or holding.quantity <= 0:
                return RiskCheckResult(allowed=False, reason=f"not_in_portfolio:{ticker}")
            sell_fraction = signal.sell_fraction if signal.sell_fraction is not None else 1.0
            sell_fraction = min(
                1.0,
                self._watchlist_multiplier(ticker, db, manager_id=manager_id) * sell_fraction,
            )
            if sell_fraction <= 0:
                return RiskCheckResult(allowed=False, reason="sell_fraction_zero")
            max_sell_notional = max(
                self.config.min_sell_notional_usd,
                resolve_max_sell_notional_usd(portfolio, self.config.max_sell_notional_pct),
            )
            sell_order = resolve_sell_order(
                holding,
                sell_fraction,
                max_trade_size_usd=max_sell_notional,
                min_trade_notional_usd=self.config.min_sell_notional_usd,
            )
            if sell_order is None or sell_order.amount_usd <= 0:
                return RiskCheckResult(allowed=False, reason="invalid_sell_size")
            normalized_trade = sell_order.amount_usd
            sell_quantity = sell_order.quantity
            is_new_ticker = False
        else:
            if self.config.live_trading_enabled and cash_available_usd is None:
                return RiskCheckResult(allowed=False, reason="insufficient_cash_data")

            normalized_trade, is_new_ticker, buy_conviction = self._resolve_trade_size(
                signal=signal,
                recognized=recognized,
                portfolio_value_usd=portfolio,
                cash_available_usd=cash_available_usd,
                db=db,
                manager_id=manager_id,
            )
            if normalized_trade <= 0 or normalized_trade < min_notional:
                return RiskCheckResult(allowed=False, reason="insufficient_cash")

        if self._tweet_already_traded(
            signal.source_tweet_id,
            db,
            manager_id=manager_id,
            ticker=ticker,
        ):
            return RiskCheckResult(allowed=False, reason=f"duplicate_tweet:{signal.source_tweet_id}:{ticker}")

        if self._daily_ticker_limit_reached(ticker, db, manager_id=manager_id):
            return RiskCheckResult(allowed=False, reason=f"daily_limit:{ticker}")

        now = datetime.now(timezone.utc)

        cooldown_cutoff = now - timedelta(seconds=self.config.cooldown_seconds)
        recent_trade = db.execute(
            select(Trade)
            .where(
                and_(
                    Trade.ticker == ticker,
                    Trade.manager_id == manager_id,
                    Trade.created_at >= cooldown_cutoff,
                )
            )
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
                    ParsedSignal.manager_id == manager_id,
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
            reason = self._buy_reason(signal, buy_conviction, normalized_trade)
        return RiskCheckResult(
            allowed=True,
            reason=reason,
            normalized_trade_usd=round(normalized_trade, 2),
            is_new_ticker=is_new_ticker,
            sell_fraction=sell_fraction,
            sell_quantity=sell_quantity,
        )

    def _resolve_trade_size(
        self,
        *,
        signal: TradeSignal,
        recognized: bool,
        portfolio_value_usd: float,
        cash_available_usd: float | None,
        db: Session,
        manager_id: str,
    ) -> tuple[float, bool, BuyConviction]:
        if signal.action != SignalAction.BUY:
            return 0.0, False, BuyConviction.STANDARD

        conviction = signal.buy_conviction or BuyConviction.STANDARD
        allocation_pct = resolve_buy_allocation_pct(
            signal,
            conviction,
            default_buy_allocation_pct=self.config.default_buy_allocation_pct,
            standard_buy_allocation_pct_max=self.config.standard_buy_allocation_pct_max,
            reload_buy_allocation_pct_max=self.config.reload_buy_allocation_pct_max,
            thesis_buy_allocation_pct_min=self.config.thesis_buy_allocation_pct_min,
            thesis_buy_allocation_pct_max=self.config.thesis_buy_allocation_pct_max,
            max_buy_allocation_pct=self.config.max_buy_allocation_pct,
        )
        watch_mult = 1.0
        if signal.ticker:
            watch_mult = self._watchlist_multiplier(signal.ticker, db, manager_id=manager_id)

        target = resolve_buy_notional_usd(
            portfolio_value_usd=portfolio_value_usd,
            allocation_pct=allocation_pct,
            cash_available_usd=cash_available_usd,
            cash_buffer_pct=self.config.cash_buffer_pct,
            min_trade_notional_pct=self.config.min_trade_notional_pct,
            min_trade_notional_usd=self.config.min_trade_notional_usd,
            watchlist_multiplier=watch_mult,
        )
        return max(0.0, target), not recognized, conviction

    def _watchlist_multiplier(self, ticker: str, db: Session, *, manager_id: str) -> float:
        entry = self.watchlist.get(ticker, db, manager_id=manager_id)
        if entry is None:
            return 1.0
        from app.parsing.watch_conviction import WatchConviction, watch_size_multiplier

        try:
            conviction = WatchConviction(entry.watch_conviction)
        except ValueError:
            conviction = WatchConviction.STANDARD
        return watch_size_multiplier(conviction, entry.conviction_score)

    @staticmethod
    def _buy_reason(
        signal: TradeSignal,
        conviction: BuyConviction | None,
        normalized_trade: float,
    ) -> str:
        if signal.portfolio_allocation_pct is not None:
            pct = int(round(signal.portfolio_allocation_pct))
            return f"allocation_{pct}pct_{int(round(normalized_trade))}"
        if conviction == BuyConviction.THESIS:
            return f"thesis_sized_{int(round(normalized_trade))}"
        if conviction == BuyConviction.RELOAD:
            return "reload_sized"
        return "standard_sized"

    def _tweet_already_traded(
        self,
        source_tweet_id: str,
        db: Session,
        *,
        manager_id: str,
        ticker: str | None = None,
    ) -> bool:
        conditions = [
            ParsedSignal.source_tweet_id == source_tweet_id,
            Trade.manager_id == manager_id,
        ]
        if ticker:
            conditions.append(Trade.ticker == ticker.upper())
        existing = db.execute(
            select(Trade.id)
            .join(ParsedSignal, Trade.parsed_signal_id == ParsedSignal.id)
            .where(and_(*conditions))
            .limit(1)
        ).scalar_one_or_none()
        return existing is not None

    def _daily_ticker_limit_reached(self, ticker: str, db: Session, *, manager_id: str) -> bool:
        if self.config.max_trades_per_ticker_per_day <= 0:
            return False

        day_start = us_trading_day_start_utc()
        query = select(Trade.id).where(
            and_(
                Trade.ticker == ticker,
                Trade.manager_id == manager_id,
                Trade.created_at >= day_start,
            )
        )
        if not self.config.daily_limit_counts_simulation:
            query = query.where(Trade.simulation.is_(False))

        trades_today = db.execute(query).all()
        return len(trades_today) >= self.config.max_trades_per_ticker_per_day


def _is_us_symbol(ticker: str) -> bool:
    return "." not in ticker
