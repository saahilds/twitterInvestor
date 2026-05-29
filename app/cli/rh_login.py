from __future__ import annotations

import argparse
import asyncio
import json

from app.config.settings import get_settings
from app.execution.robinhood_broker import RobinhoodBroker
from app.risk.market_hours import is_within_regular_market_hours
from app.runtime import build_logger


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    logger = build_logger(settings)

    if settings.broker_backend != "robinhood":
        print(json.dumps({"ok": False, "error": f"broker_backend_is_{settings.broker_backend}"}, indent=2))
        return 1

    broker = RobinhoodBroker(settings=settings, logger=logger)

    if args.list_accounts:
        login_ok = await asyncio.to_thread(broker._login)
        if not login_ok:
            print(json.dumps({"ok": False, "error": "robinhood_login_failed"}, indent=2))
            return 1
        accounts = await asyncio.to_thread(broker.list_accounts)
        print(
            json.dumps(
                {
                    "ok": True,
                    "accounts": accounts,
                    "hint": "Set ROBINHOOD_ACCOUNT=individual, joint, or an account_number from the list.",
                },
                indent=2,
                default=str,
            )
        )
        return 0

    if args.verify_all_accounts:
        result = await broker.verify_all_accounts()
        payload = {
            **result,
            "within_market_hours": is_within_regular_market_hours(),
            "live_trading_enabled": settings.live_trading_enabled,
            "note": "Login + balance snapshot per account; no order placed.",
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0 if result.get("ok") else 1

    result = await broker.verify_login()

    payload = {
        **result,
        "within_market_hours": is_within_regular_market_hours(),
        "live_trading_enabled": settings.live_trading_enabled,
        "note": "Login check only; no order placed.",
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0 if result.get("ok") else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Robinhood login using .env credentials (any time of day).",
    )
    parser.add_argument(
        "--list-accounts",
        action="store_true",
        help="List brokerage accounts (individual/joint) and account numbers.",
    )
    parser.add_argument(
        "--verify-all-accounts",
        action="store_true",
        help="Log in and print buying power / equity for every linked account.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
