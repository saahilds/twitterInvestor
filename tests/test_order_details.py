from app.execution.order_details import (
    enrich_broker_order_result,
    extract_error_message,
    map_robinhood_state,
)
from app.models.schemas import BrokerOrderResult


def test_map_robinhood_state() -> None:
    assert map_robinhood_state("filled") == "filled"
    assert map_robinhood_state("queued") == "open"
    assert map_robinhood_state("cancelled") == "cancelled"


def test_enrich_limit_at_ask_response() -> None:
    result = enrich_broker_order_result(
        BrokerOrderResult(
            status="submitted",
            order_id="abc",
            simulation=False,
            quantity=0.01,
            raw_response={
                "order_type": "limit_at_ask",
                "ask": 150.25,
                "limit_price": 150.25,
                "broker_response": {"state": "queued", "price": "150.2500", "quantity": "0.01"},
            },
        ),
        order_execution_mode="limit_at_ask",
    )
    assert result.order_type == "limit_at_ask"
    assert result.ask_price == 150.25
    assert result.limit_price == 150.25
    assert result.status == "open"


def test_enrich_failed_order_sets_error_message() -> None:
    raw = {"error": "invalid_ask_price:FOO", "order_type": "limit_at_ask"}
    assert extract_error_message(raw) == "invalid_ask_price:FOO"
    result = enrich_broker_order_result(
        BrokerOrderResult(status="failed", simulation=False, raw_response=raw),
        order_execution_mode="limit_at_ask",
    )
    assert result.error_message == "invalid_ask_price:FOO"


def test_enrich_broker_rejection_detail_marks_failed() -> None:
    result = enrich_broker_order_result(
        BrokerOrderResult(
            status="submitted",
            simulation=False,
            raw_response={
                "order_type": "fractional_market",
                "side": "sell",
                "broker_response": {"detail": "Not enough shares to sell."},
            },
        ),
        order_execution_mode="fractional_market",
    )
    assert result.status == "failed"
    assert result.error_message == "Not enough shares to sell."


def test_create_trade_record_fields() -> None:
    from app.models.db_models import SignalAction
    from app.services.trade_recorder import create_trade_record

    trade = create_trade_record(
        parsed_signal_id=1,
        source_tweet_id="tweet-1",
        ticker="SPY",
        action=SignalAction.BUY,
        amount_usd=1.0,
        order_result=enrich_broker_order_result(
            BrokerOrderResult(
                status="simulated",
                order_id="sim-1",
                simulation=True,
                quantity=0.01,
                raw_response={"order_type": "limit_at_ask", "ask": 100.0, "limit_price": 100.0},
            ),
            order_execution_mode="limit_at_ask",
        ),
        order_execution_mode="limit_at_ask",
        manager_id="individual",
    )
    assert trade.source_tweet_id == "tweet-1"
    assert trade.limit_price == 100.0
    assert trade.order_type == "limit_at_ask"
    assert trade.status == "simulated"
