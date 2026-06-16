from __future__ import annotations

import argparse
import json

from app.config.settings import get_settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.portfolio.quotes import QuoteProvider
from app.runtime import build_logger
from app.services.pnl_service import PnlService


def _print_table(report) -> None:
    header = (
        f"{'Ticker':<8} {'Shares':>10} {'AvgCost':>10} {'Last':>10} "
        f"{'Realized':>10} {'Unrealized':>12} {'Total':>10}"
    )
    print(header)
    print("-" * len(header))
    for row in report.tickers:
        last = f"{row.last_price:.2f}" if row.last_price is not None else "n/a"
        unreal = f"{row.unrealized_pnl:+.2f}" if row.unrealized_pnl is not None else "n/a"
        print(
            f"{row.ticker:<8} {row.shares_held:>10.4f} {row.avg_cost_basis:>10.2f} {last:>10} "
            f"{row.realized_pnl:>+10.2f} {unreal:>12} {row.total_pnl:>+10.2f}"
        )
    print("-" * len(header))
    print(
        f"{'TOTAL':<8} {'':>10} {'':>10} {'':>10} "
        f"{report.realized_pnl_total:>+10.2f} {report.unrealized_pnl_total:>+12.2f} {report.total_pnl:>+10.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="P&L by ticker from trades DB + live quotes.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    parser.add_argument("--no-live-prices", action="store_true", help="Skip Robinhood quote fetch.")
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Exclude simulated trades from position math.",
    )
    args = parser.parse_args()

    init_db()
    settings = get_settings()
    logger = build_logger(settings)
    service = PnlService(
        session_factory=SessionLocal,
        quote_provider=QuoteProvider(settings=settings, logger=logger),
        include_simulation=not args.live_only,
        quote_cache_seconds=settings.pnl_quote_cache_seconds,
    )
    report = service.build_report(fetch_live_prices=not args.no_live_prices)

    if args.json:
        print(json.dumps(report.model_dump(), indent=2))
    else:
        _print_table(report)


if __name__ == "__main__":
    main()
