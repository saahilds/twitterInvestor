from app.execution.holdings import BrokerPortfolioMetrics, resolve_stocks_plus_cash


def test_resolve_stocks_plus_cash_prefers_equity() -> None:
    assert (
        resolve_stocks_plus_cash(
            portfolio_equity=6836.35,
            profile_market_value=6390.69,
            cash=400.04,
        )
        == 6836.35
    )


def test_resolve_stocks_plus_cash_falls_back_to_market_plus_cash() -> None:
    assert (
        resolve_stocks_plus_cash(
            portfolio_equity=None,
            profile_market_value=6390.69,
            cash=400.04,
        )
        == 6390.69 + 400.04
    )


def test_broker_portfolio_metrics_stocks_plus_cash_field() -> None:
    metrics = BrokerPortfolioMetrics(
        stocks_plus_cash=1500.0,
        portfolio_equity=1500.0,
        positions_market_value=700.0,
        profile_market_value=705.0,
        cash=800.0,
    )
    assert metrics.stocks_plus_cash == 1500.0
