import pytest

from app.execution.robinhood_accounts import (
    _pick_balance_fields,
    resolve_account_number,
    summarize_account,
)


def test_resolve_individual_account() -> None:
    accounts = [
        {
            "account_number": "111",
            "brokerage_account_type": "individual",
        },
        {
            "account_number": "222",
            "brokerage_account_type": "joint_tenancy_with_ros",
        },
    ]
    assert resolve_account_number("individual", accounts) == "111"
    assert resolve_account_number("joint", accounts) == "222"


def test_resolve_explicit_account_number() -> None:
    accounts = [
        {"account_number": "111", "brokerage_account_type": "individual"},
        {"account_number": "222", "brokerage_account_type": "joint_tenancy_with_ros"},
    ]
    assert resolve_account_number("222", accounts) == "222"


def test_resolve_missing_account_raises() -> None:
    accounts = [{"account_number": "111", "brokerage_account_type": "individual"}]
    with pytest.raises(ValueError, match="account_not_found:joint"):
        resolve_account_number("joint", accounts)


def test_summarize_account_includes_type() -> None:
    summary = summarize_account(
        {
            "account_number": "111",
            "brokerage_account_type": "individual",
            "type": "margin",
        }
    )
    assert summary["account_number"] == "111"
    assert summary["brokerage_account_type"] == "individual"


def test_pick_balance_fields_omits_missing() -> None:
    picked = _pick_balance_fields(
        {"buying_power": "100.00", "cash": None, "equity": "250.00"},
        ("buying_power", "cash", "equity"),
    )
    assert picked == {"buying_power": "100.00", "equity": "250.00"}
