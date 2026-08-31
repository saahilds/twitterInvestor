from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.db_models import ParsedSignal, SignalAction, Trade, Tweet
from app.models.schemas import TradeSignal
from app.parsing.factory import SignalParser
from app.risk.risk_manager import RiskManager


@dataclass(slots=True)
class ReplaySignalRow:
    action: str
    ticker: str | None
    confidence: float
    buy_conviction: str | None
    sell_fraction: float | None
    portfolio_allocation_pct: float | None
    risk_allowed: bool | None
    risk_reason: str | None
    normalized_trade_usd: float | None
    would_trade: bool
    risk_blocked: bool


@dataclass(slots=True)
class StoredSignalRow:
    action: str
    ticker: str | None
    confidence: float
    rejection_reason: str | None
    manager_id: str


@dataclass(slots=True)
class ReplayTweetResult:
    tweet_id: str
    posted_at: datetime
    text_snippet: str
    replayed: list[ReplaySignalRow]
    stored: list[StoredSignalRow]
    traded: bool
    traded_live: bool
    parser_drift: bool
    mismatch: bool


def _snippet(text: str, limit: int = 120) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _normalize_signals(parsed) -> list[TradeSignal]:
    if isinstance(parsed, list):
        return parsed
    return [parsed]


class SignalReplayService:
    """Re-run parser (+ optional risk) over stored tweets. DB read + in-process only."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        parser: SignalParser,
        risk_manager: RiskManager | None = None,
        *,
        manager_id: str = "individual",
    ) -> None:
        self.session_factory = session_factory
        self.parser = parser
        self.risk_manager = risk_manager
        self.manager_id = manager_id

    def replay(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 500,
        compare_stored: bool = False,
        include_risk: bool = True,
        assume_cash: float | None = None,
        only_mismatches: bool = False,
    ) -> list[ReplayTweetResult]:
        with self.session_factory() as db:
            stmt = (
                select(Tweet)
                .options(selectinload(Tweet.parsed_signals).selectinload(ParsedSignal.trades))
                .order_by(Tweet.posted_at.desc())
                .limit(limit)
            )
            if since is not None:
                stmt = stmt.where(Tweet.posted_at >= since)
            if until is not None:
                stmt = stmt.where(Tweet.posted_at <= until)
            tweets = list(db.execute(stmt).scalars().all())

            known: set[str] = set()
            if self.risk_manager is not None:
                known |= self.risk_manager.registry.union_tickers(db, [self.manager_id])
                known |= self.risk_manager.watchlist.union_tickers(db, [self.manager_id])

            results: list[ReplayTweetResult] = []
            for tweet in tweets:
                results.append(
                    self._replay_tweet(
                        db,
                        tweet,
                        known_tickers=known,
                        compare_stored=compare_stored,
                        include_risk=include_risk,
                        assume_cash=assume_cash,
                    )
                )

        if only_mismatches:
            return [row for row in results if row.mismatch]
        return results

    def _replay_tweet(
        self,
        db: Session,
        tweet: Tweet,
        *,
        known_tickers: set[str],
        compare_stored: bool,
        include_risk: bool,
        assume_cash: float | None,
    ) -> ReplayTweetResult:
        signals = _normalize_signals(
            self.parser.parse(
                tweet.text,
                source_tweet_id=tweet.tweet_id,
                extra_known_tickers=known_tickers,
            )
        )
        stored_rows: list[StoredSignalRow] = []
        if compare_stored:
            for signal in tweet.parsed_signals:
                stored_rows.append(
                    StoredSignalRow(
                        action=signal.action.value,
                        ticker=signal.ticker,
                        confidence=signal.confidence,
                        rejection_reason=signal.rejection_reason,
                        manager_id=signal.manager_id,
                    )
                )

        trades = [trade for signal in tweet.parsed_signals for trade in signal.trades]
        traded = bool(trades)
        traded_live = any(not trade.simulation for trade in trades)

        replayed: list[ReplaySignalRow] = []
        for signal in signals:
            risk_allowed: bool | None = None
            risk_reason: str | None = None
            normalized: float | None = None
            if include_risk and self.risk_manager is not None and signal.action not in {
                SignalAction.IGNORE,
                SignalAction.WATCH,
            }:
                cash = assume_cash
                portfolio = assume_cash
                result = self.risk_manager.evaluate(
                    signal,
                    db,
                    manager_id=self.manager_id,
                    cash_available_usd=cash,
                    portfolio_value_usd=portfolio,
                    holding=None,
                    as_of=tweet.posted_at,
                )
                risk_allowed = result.allowed
                risk_reason = result.reason
                normalized = result.normalized_trade_usd

            would_trade = bool(risk_allowed) if risk_allowed is not None else signal.action in {
                SignalAction.BUY,
                SignalAction.SELL,
            }
            risk_blocked = risk_allowed is False
            replayed.append(
                ReplaySignalRow(
                    action=signal.action.value,
                    ticker=signal.ticker,
                    confidence=signal.confidence,
                    buy_conviction=signal.buy_conviction.value if signal.buy_conviction else None,
                    sell_fraction=signal.sell_fraction,
                    portfolio_allocation_pct=signal.portfolio_allocation_pct,
                    risk_allowed=risk_allowed,
                    risk_reason=risk_reason,
                    normalized_trade_usd=normalized,
                    would_trade=would_trade and not risk_blocked,
                    risk_blocked=risk_blocked,
                )
            )

        parser_drift = False
        if compare_stored and stored_rows:
            stored_actions = {(row.action, (row.ticker or "").upper()) for row in stored_rows}
            replay_actions = {(row.action, (row.ticker or "").upper()) for row in replayed}
            # Ignore pure IGNORE-only stored noise when replay also empty of trades
            parser_drift = stored_actions != replay_actions

        mismatch = parser_drift
        if traded and not any(row.would_trade for row in replayed):
            mismatch = True
        if any(row.would_trade for row in replayed) and not traded and any(
            row.risk_blocked for row in replayed
        ):
            # risk-blocked today while historically may differ — not always mismatch
            pass
        if compare_stored and parser_drift:
            mismatch = True

        return ReplayTweetResult(
            tweet_id=tweet.tweet_id,
            posted_at=tweet.posted_at if tweet.posted_at.tzinfo else tweet.posted_at.replace(tzinfo=timezone.utc),
            text_snippet=_snippet(tweet.text),
            replayed=replayed,
            stored=stored_rows if compare_stored else [],
            traded=traded,
            traded_live=traded_live,
            parser_drift=parser_drift,
            mismatch=mismatch,
        )


def replay_result_to_dict(row: ReplayTweetResult) -> dict:
    payload = asdict(row)
    payload["posted_at"] = row.posted_at.isoformat()
    return payload
