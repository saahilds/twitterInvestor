from app.execution.buying_power import cash_available_from_account_profile


def test_cash_available_prefers_cash_over_buying_power() -> None:
    amount = cash_available_from_account_profile(
        {
            "cash": "25.50",
            "buying_power": "500.00",
        }
    )
    assert amount == 25.5


def test_cash_available_ignores_buying_power_when_cash_missing() -> None:
    amount = cash_available_from_account_profile({"buying_power": "500.00"})
    assert amount is None
