from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.orm import Session

from app.execution.order_details import apply_robinhood_order_info, map_robinhood_state
from app.execution.robinhood_broker import RobinhoodBroker
from app.models.db_models import Trade, utc_now
from app.models.schemas import BrokerOrderResult


class TradeStatusSync:
    """Refresh open trade rows from Robinhood order status."""

    def __init__(self, broker: RobinhoodBroker, logger: logging.Logger) -> None:
        self.broker = broker
        self.logger = logger

    async def refresh(self, db: Session, trade: Trade) -> Trade:
        if trade.simulation or not trade.broker_order_id:
            return trade

        order_info = await asyncio.to_thread(self.broker.fetch_order_info, trade.broker_order_id)
        if not order_info:
            return trade

        current = BrokerOrderResult(
            status=trade.status,
            order_id=trade.broker_order_id,
            simulation=trade.simulation,
            quantity=trade.quantity,
            order_type=trade.order_type,
            ask_price=trade.ask_price,
            limit_price=trade.limit_price,
            fill_price=trade.fill_price,
            account_number=trade.account_number,
            raw_response=json.loads(trade.response_json) if trade.response_json else {},
        )
        enriched = apply_robinhood_order_info(current, order_info)
        trade.status = enriched.status
        trade.quantity = enriched.quantity
        trade.limit_price = enriched.limit_price
        trade.fill_price = enriched.fill_price
        trade.updated_at = utc_now()
        trade.response_json = json.dumps(enriched.raw_response or {}, default=str)
        db.add(trade)
        self.logger.info(
            "trade_status_refreshed",
            extra={
                "event_type": "trade_status",
                "trade_id": trade.id,
                "broker_order_id": trade.broker_order_id,
                "status": trade.status,
                "fill_price": trade.fill_price,
                "robinhood_state": order_info.get("state"),
            },
        )
        return trade


def trade_is_terminal(status: str) -> bool:
    return status in {"filled", "cancelled", "rejected", "failed", "simulated"}


def status_from_order_info(order_info: dict) -> str:
    return map_robinhood_state(order_info.get("state"))
