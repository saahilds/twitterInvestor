from __future__ import annotations


def parse_money(value: object, *, allow_zero: bool = True) -> float | None:
    if value is None:
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if allow_zero:
        return amount if amount >= 0 else None
    return amount if amount > 0 else None


def parse_cash_amount(value: object) -> float | None:
    return parse_money(value, allow_zero=False)


def cash_available_from_account_profile(profile: dict) -> float | None:
    """Return spendable cash only — never buying_power (margin)."""
    if not isinstance(profile, dict):
        return None

    for key in ("cash", "portfolio_cash", "cash_available_for_withdrawal"):
        amount = parse_cash_amount(profile.get(key))
        if amount is not None:
            return amount
    return None


def fetch_robinhood_cash_available_usd(account_number: str | None = None) -> float | None:
    try:
        from robin_stocks import robinhood as rh
    except Exception:
        return None

    profile = rh.profiles.load_account_profile(account_number=account_number)
    if isinstance(profile, list) and profile:
        profile = profile[0]
    return cash_available_from_account_profile(profile if isinstance(profile, dict) else {})
