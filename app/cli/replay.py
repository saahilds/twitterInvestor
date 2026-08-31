from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from app.config.account_managers import default_manager_id, parse_bot_managers
from app.config.settings import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.parsing.factory import build_signal_parser
from app.risk.risk_manager import RiskConfig, RiskManager
from app.services.signal_replay import SignalReplayService, replay_result_to_dict
from app.services.watchlist import WatchlistRegistry


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        moment = datetime.strptime(text, "%Y-%m-%d")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _build_risk_config(settings) -> RiskConfig:
    return RiskConfig(
        default_buy_allocation_pct=settings.default_buy_allocation_pct,
        standard_buy_allocation_pct_max=settings.standard_buy_allocation_pct_max,
        reload_buy_allocation_pct_max=settings.reload_buy_allocation_pct_max,
        thesis_buy_allocation_pct_min=settings.thesis_buy_allocation_pct_min,
        thesis_buy_allocation_pct_max=settings.thesis_buy_allocation_pct_max,
        min_trade_notional_pct=settings.min_trade_notional_pct,
        min_trade_notional_usd=settings.min_trade_notional_usd,
        cash_buffer_pct=settings.cash_buffer_pct,
        max_sell_notional_pct=settings.max_sell_notional_pct,
        ck_portfolio_usd=settings.resolved_ck_portfolio_usd,
        cooldown_seconds=settings.cooldown_seconds,
        duplicate_window_seconds=settings.duplicate_window_seconds,
        trading_window_enabled=settings.trading_window_enabled,
        us_symbols_only=settings.us_symbols_only,
        max_trades_per_ticker_per_day=settings.max_trades_per_ticker_per_day,
        daily_limit_counts_simulation=settings.daily_limit_counts_simulation,
        live_trading_enabled=False,
        min_sell_notional_usd=settings.min_sell_notional_usd,
        watchlist_stale_days=settings.watchlist_stale_days,
        watchlist_max_conviction_score=settings.watchlist_max_conviction_score,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay parser (+ risk) over stored tweets.")
    parser.add_argument("--since", help="ISO date/datetime lower bound on posted_at")
    parser.add_argument("--until", help="ISO date/datetime upper bound on posted_at")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only-mismatches", action="store_true")
    parser.add_argument("--compare-stored", action="store_true")
    parser.add_argument("--no-risk", action="store_true")
    parser.add_argument("--assume-cash", type=float, default=None)
    parser.add_argument("--manager", default=None, help="Manager id for risk/registry scope")
    args = parser.parse_args()

    init_db()
    settings = get_settings()
    managers = parse_bot_managers(settings)
    manager_id = args.manager or default_manager_id(settings, managers)

    signal_parser = build_signal_parser(settings)
    risk_manager = RiskManager(
        _build_risk_config(settings),
        watchlist=WatchlistRegistry(
            max_conviction_score=settings.watchlist_max_conviction_score,
            stale_days=settings.watchlist_stale_days,
        ),
    )
    service = SignalReplayService(
        session_factory=SessionLocal,
        parser=signal_parser,
        risk_manager=None if args.no_risk else risk_manager,
        manager_id=manager_id,
    )
    rows = service.replay(
        since=_parse_date(args.since),
        until=_parse_date(args.until),
        limit=args.limit,
        compare_stored=args.compare_stored,
        include_risk=not args.no_risk,
        assume_cash=args.assume_cash,
        only_mismatches=args.only_mismatches,
    )

    if args.json:
        print(json.dumps([replay_result_to_dict(row) for row in rows], indent=2))
        return

    print(f"{'tweet_id':<22} {'posted':<20} {'replay':<18} {'stored':<18} flags")
    print("-" * 100)
    for row in rows:
        replay_desc = ",".join(f"{s.action}:{s.ticker or '-'}" for s in row.replayed) or "—"
        stored_desc = ",".join(f"{s.action}:{s.ticker or '-'}" for s in row.stored) or (
            "—" if args.compare_stored else ""
        )
        flags = []
        if row.parser_drift:
            flags.append("drift")
        if row.traded_live:
            flags.append("live")
        elif row.traded:
            flags.append("sim")
        if any(s.risk_blocked for s in row.replayed):
            flags.append("risk_blocked")
        if row.mismatch:
            flags.append("mismatch")
        print(
            f"{row.tweet_id:<22} {row.posted_at.isoformat():<20} "
            f"{replay_desc[:18]:<18} {stored_desc[:18]:<18} {','.join(flags)}"
        )
        print(f"  {row.text_snippet}")
    print(f"\n{len(rows)} tweets")


if __name__ == "__main__":
    main()
