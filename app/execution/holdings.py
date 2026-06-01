from __future__ import annotations

from dataclasses import dataclass

from app.execution.buying_power import parse_cash_amount, parse_money


@dataclass(slots=True)
class BrokerPortfolioMetrics:
    """Robinhood account totals. Use ``stocks_plus_cash`` as the full account balance."""

    stocks_plus_cash: float | None
    portfolio_equity: float | None
    positions_market_value: float | None
    profile_market_value: float | None
    cash: float | None


def resolve_stocks_plus_cash(
    *,
    portfolio_equity: float | None,
    profile_market_value: float | None,
    cash: float | None,
    positions_market_value: float | None = None,
) -> float | None:
    """Account balance = equities + cash (Robinhood ``equity`` when available)."""
    if portfolio_equity is not None:
        return portfolio_equity
    if profile_market_value is not None and cash is not None:
        return profile_market_value + cash
    if positions_market_value is not None and cash is not None:
        return positions_market_value + cash
    return None


@dataclass(slots=True)
class BrokerHolding:
    ticker: str
    quantity: float
    average_cost: float
    last_price: float | None
    market_value: float | None
    cost_basis: float
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None


def parse_position_row(
    *,
    ticker: str,
    quantity: float,
    average_cost: float,
    last_price: float | None,
) -> BrokerHolding:
    cost_basis = quantity * average_cost
    market_value = (quantity * last_price) if last_price is not None else None
    unrealized = (market_value - cost_basis) if market_value is not None else None
    unrealized_pct = (
        (unrealized / cost_basis) * 100.0 if unrealized is not None and cost_basis > 0 else None
    )
    return BrokerHolding(
        ticker=ticker.upper(),
        quantity=quantity,
        average_cost=average_cost,
        last_price=last_price,
        market_value=market_value,
        cost_basis=round(cost_basis, 4),
        unrealized_pnl=round(unrealized, 4) if unrealized is not None else None,
        unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
    )


def fetch_robinhood_holdings(account_number: str | None = None) -> tuple[list[BrokerHolding], str | None]:
    """Return open stock positions from Robinhood for the selected account."""
    try:
        from robin_stocks import robinhood as rh
    except Exception:
        return [], "robin_stocks_unavailable"

    try:
        positions = rh.account.get_open_stock_positions(account_number=account_number)
    except Exception as exc:
        return [], str(exc)

    if not isinstance(positions, list):
        return [], "invalid_positions_response"

    holdings: list[BrokerHolding] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        if account_number and str(item.get("account_number", "")) != str(account_number):
            continue

        quantity = parse_cash_amount(item.get("quantity"))
        average_cost = parse_cash_amount(item.get("average_buy_price"))
        if quantity is None or quantity <= 0 or average_cost is None:
            continue

        instrument_url = item.get("instrument")
        if not isinstance(instrument_url, str):
            continue

        try:
            instrument = rh.stocks.get_instrument_by_url(instrument_url)
            ticker = instrument.get("symbol") if isinstance(instrument, dict) else None
        except Exception:
            ticker = None
        if not ticker:
            continue

        last_price = None
        try:
            latest = rh.stocks.get_latest_price(ticker, includeExtendedHours=True)
            if isinstance(latest, list) and latest:
                last_price = parse_cash_amount(latest[0])
            elif isinstance(latest, str):
                last_price = parse_cash_amount(latest)
        except Exception:
            last_price = None

        holdings.append(
            parse_position_row(
                ticker=str(ticker),
                quantity=quantity,
                average_cost=average_cost,
                last_price=last_price,
            )
        )

    holdings.sort(key=lambda row: row.ticker)
    return holdings, None


def fetch_portfolio_metrics(account_number: str | None = None) -> BrokerPortfolioMetrics:
    """Load Robinhood portfolio profile; ``stocks_plus_cash`` is the app account total."""
    try:
        from robin_stocks import robinhood as rh
    except Exception:
        return BrokerPortfolioMetrics(None, None, None, None, None)

    try:
        portfolio = rh.profiles.load_portfolio_profile(account_number=account_number)
        account = rh.profiles.load_account_profile(account_number=account_number)
    except Exception:
        return BrokerPortfolioMetrics(None, None, None, None, None)

    if isinstance(portfolio, list) and portfolio:
        portfolio = portfolio[0]
    if isinstance(account, list) and account:
        account = account[0]

    portfolio_dict = portfolio if isinstance(portfolio, dict) else {}
    account_dict = account if isinstance(account, dict) else {}

    equity = parse_money(portfolio_dict.get("equity"))
    extended_equity = parse_money(portfolio_dict.get("extended_hours_equity"))
    if equity is not None and extended_equity is not None:
        portfolio_equity = max(equity, extended_equity)
    else:
        portfolio_equity = equity if equity is not None else extended_equity

    cash = parse_money(account_dict.get("cash"))
    if cash is None:
        cash = parse_money(account_dict.get("portfolio_cash"))

    profile_market_value = parse_money(portfolio_dict.get("market_value"))
    stocks_plus_cash = resolve_stocks_plus_cash(
        portfolio_equity=portfolio_equity,
        profile_market_value=profile_market_value,
        cash=cash,
    )

    return BrokerPortfolioMetrics(
        stocks_plus_cash=stocks_plus_cash,
        portfolio_equity=portfolio_equity,
        positions_market_value=None,
        profile_market_value=profile_market_value,
        cash=cash,
    )
