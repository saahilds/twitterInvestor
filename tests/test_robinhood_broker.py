import logging
from types import SimpleNamespace

from app.config.settings import Settings
from app.execution.robinhood_broker import RobinhoodBroker


def test_submit_buy_order_uses_instance_account_number(monkeypatch) -> None:
    captured: dict = {}

    def fake_buy(**kwargs):
        captured.update(kwargs)
        return {"id": "order-1"}

    monkeypatch.setattr(
        "app.execution.robinhood_broker.rh",
        SimpleNamespace(
            orders=SimpleNamespace(
                order_buy_fractional_by_price=fake_buy,
            )
        ),
    )

    broker = RobinhoodBroker(settings=Settings(), logger=logging.getLogger("test"))
    broker._account_number = "116195792456"

    result = broker._submit_buy_order("ASTS", 25.0)

    assert result["id"] == "order-1"
    assert captured["account_number"] == "116195792456"
    assert captured["symbol"] == "ASTS"
    assert captured["amountInDollars"] == 25.0


def test_submit_sell_order_uses_share_quantity(monkeypatch) -> None:
    captured: dict = {}

    def fake_sell_qty(**kwargs):
        captured.update(kwargs)
        return {"id": "order-sell-1"}

    monkeypatch.setattr(
        "app.execution.robinhood_broker.rh",
        SimpleNamespace(
            orders=SimpleNamespace(
                order_sell_fractional_by_quantity=fake_sell_qty,
            )
        ),
    )

    broker = RobinhoodBroker(settings=Settings(), logger=logging.getLogger("test"))
    broker._account_number = "116195792456"

    result = broker._submit_sell_order("ASTS", 1.25)

    assert result["id"] == "order-sell-1"
    assert captured["quantity"] == 1.25
    assert captured["account_number"] == "116195792456"
    assert "amountInDollars" not in captured
