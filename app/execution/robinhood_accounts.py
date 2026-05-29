from __future__ import annotations

from typing import Any


def load_all_accounts() -> list[dict[str, Any]]:
    from robin_stocks import robinhood as rh

    data = rh.profiles.load_account_profile(dataType="results")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def summarize_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_number": account.get("account_number"),
        "brokerage_account_type": account.get("brokerage_account_type"),
        "type": account.get("type"),
        "nickname": account.get("nickname"),
        "rhs_account_number": account.get("rhs_account_number"),
    }


def resolve_account_number(selector: str | None, accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        raise ValueError("no_robinhood_accounts")

    if not selector or not selector.strip():
        number = accounts[0].get("account_number")
        if not number:
            raise ValueError("account_number_missing")
        return str(number)

    choice = selector.strip()
    for account in accounts:
        if str(account.get("account_number", "")) == choice:
            return choice

    choice_lower = choice.lower()
    if choice_lower in {"individual", "joint"}:
        matched = [_match_account_kind(account, choice_lower) for account in accounts]
        hits = [account for account, ok in zip(accounts, matched) if ok]
        if len(hits) == 1:
            number = hits[0].get("account_number")
            if not number:
                raise ValueError("account_number_missing")
            return str(number)
        if len(hits) > 1:
            options = [summarize_account(account) for account in hits]
            raise ValueError(f"ambiguous_account:{choice}:{options}")

    raise ValueError(f"account_not_found:{choice}")


def _match_account_kind(account: dict[str, Any], kind: str) -> bool:
    brokerage_type = str(account.get("brokerage_account_type", "")).lower()
    if kind == "individual":
        return brokerage_type == "individual"
    if kind == "joint":
        return "joint" in brokerage_type
    return False


_ACCOUNT_BALANCE_KEYS = (
    "buying_power",
    "cash",
    "portfolio_cash",
    "cash_available_for_withdrawal",
    "cash_held_for_orders",
    "crypto_buying_power",
)

_PORTFOLIO_BALANCE_KEYS = (
    "market_value",
    "equity",
    "withdrawable_amount",
    "last_core_equity",
)


def _pick_balance_fields(source: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source.get(key) for key in keys if source.get(key) is not None}


def fetch_account_snapshot(account_number: str, account_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch non-sensitive balance fields for one brokerage account."""
    from robin_stocks import robinhood as rh

    account_profile = rh.profiles.load_account_profile(account_number=account_number)
    portfolio_profile = rh.profiles.load_portfolio_profile(account_number=account_number)

    meta = account_meta or {}
    return {
        **summarize_account(meta),
        "account_number": account_number,
        "balances": {
            **_pick_balance_fields(account_profile if isinstance(account_profile, dict) else None, _ACCOUNT_BALANCE_KEYS),
            **_pick_balance_fields(
                portfolio_profile if isinstance(portfolio_profile, dict) else None,
                _PORTFOLIO_BALANCE_KEYS,
            ),
        },
    }


def fetch_all_account_snapshots(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for account in accounts:
        account_number = account.get("account_number")
        if not account_number:
            snapshots.append(
                {
                    "ok": False,
                    "error": "account_number_missing",
                    "account": summarize_account(account),
                }
            )
            continue
        number = str(account_number)
        try:
            snapshots.append({"ok": True, **fetch_account_snapshot(number, account)})
        except Exception as exc:
            snapshots.append(
                {
                    "ok": False,
                    "account_number": number,
                    **summarize_account(account),
                    "error": str(exc),
                }
            )
    return snapshots
