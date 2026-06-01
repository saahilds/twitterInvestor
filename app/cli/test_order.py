from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config.settings import get_settings
from app.execution.mock_broker import MockBroker
from app.execution.robinhood_broker import RobinhoodBroker
from app.risk.market_hours import is_within_regular_market_hours
from app.runtime import build_logger


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = build_logger(settings)

    if settings.trading_window_enabled and not is_within_regular_market_hours():
        payload = {"error": "outside_market_hours", "ticker": args.ticker.upper()}
        print(json.dumps(payload, indent=2))
        return 1

    if settings.broker_backend == "mock":
        broker = MockBroker()
    else:
        broker = RobinhoodBroker(settings=settings, logger=logger)

    result = await broker.buy_limit_at_ask(ticker=args.ticker.upper(), amount_usd=args.amount)

    payload = {
        "ticker": args.ticker.upper(),
        "amount_usd": args.amount,
        "live_trading_enabled": settings.live_trading_enabled,
        "status": result.status,
        "simulation": result.simulation,
        "order_id": result.order_id,
        "quantity": result.quantity,
        "response": result.raw_response,
    }
    print(json.dumps(payload, indent=2, default=str))

    if result.status == "failed":
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Place a single $1 limit-at-ask buy to validate Robinhood credentials.",
    )
    parser.add_argument("--ticker", default="SPY", help="US equity symbol (default SPY)")
    parser.add_argument("--amount", type=float, default=1.0, help="Dollar amount (default 1.0)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
