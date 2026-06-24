from __future__ import annotations

import json

from app.execution.order_details import enrich_broker_order_result
from app.models.db_models import SignalAction, Trade, utc_now
from app.models.schemas import BrokerOrderResult


def create_trade_record(
    *,
    parsed_signal_id: int,
    source_tweet_id: str,
    ticker: str,
    action: SignalAction,
    amount_usd: float,
    order_result: BrokerOrderResult,
    order_execution_mode: str,
    manager_id: str,
) -> Trade:
    enriched = enrich_broker_order_result(
        order_result,
        order_execution_mode=order_execution_mode,
    )
    now = utc_now()
    return Trade(
        parsed_signal_id=parsed_signal_id,
        source_tweet_id=source_tweet_id,
        ticker=ticker,
        action=action,
        amount_usd=amount_usd,
        quantity=enriched.quantity,
        status=enriched.status,
        simulation=enriched.simulation,
        broker_order_id=enriched.order_id,
        order_type=enriched.order_type,
        ask_price=enriched.ask_price,
        limit_price=enriched.limit_price,
        fill_price=enriched.fill_price,
        error_message=enriched.error_message,
        account_number=enriched.account_number,
        manager_id=manager_id,
        response_json=json.dumps(enriched.raw_response or {}, default=str),
        created_at=now,
        updated_at=now,
    )
