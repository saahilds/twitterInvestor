from __future__ import annotations

from typing import Any

from app.models.schemas import BrokerOrderResult

_ROBINHOOD_FILLED_STATES = {"filled", "completed"}
_ROBINHOOD_CANCELLED_STATES = {"cancelled", "canceled"}
_ROBINHOOD_REJECTED_STATES = {"rejected", "failed", "voided"}
_ROBINHOOD_OPEN_STATES = {"queued", "unconfirmed", "confirmed", "partially_filled", "open"}


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _broker_payload(raw_response: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw_response:
        return None
    nested = raw_response.get("broker_response")
    if isinstance(nested, dict):
        return nested
    if isinstance(nested, list) and nested and isinstance(nested[0], dict):
        return nested[0]
    if "state" in raw_response or "id" in raw_response:
        return raw_response
    return None


def map_robinhood_state(state: str | None) -> str:
    normalized = (state or "").strip().lower()
    if not normalized:
        return "submitted"
    if normalized in _ROBINHOOD_FILLED_STATES:
        return "filled"
    if normalized in _ROBINHOOD_CANCELLED_STATES:
        return "cancelled"
    if normalized in _ROBINHOOD_REJECTED_STATES:
        return "rejected"
    if normalized in _ROBINHOOD_OPEN_STATES:
        return "open"
    return normalized


def extract_error_message(raw_response: dict[str, Any] | None) -> str | None:
    if not raw_response:
        return None
    for key in ("error", "detail", "message"):
        value = raw_response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def enrich_broker_order_result(
    result: BrokerOrderResult,
    *,
    order_execution_mode: str,
) -> BrokerOrderResult:
    """Fill structured price/status fields from broker raw_response."""
    raw = result.raw_response or {}
    order_type = raw.get("order_type") or order_execution_mode
    ask_price = _as_float(raw.get("ask"))
    limit_price = _as_float(raw.get("limit_price"))

    broker = _broker_payload(raw)
    if broker:
        if limit_price is None:
            limit_price = _as_float(broker.get("price"))
        status = map_robinhood_state(broker.get("state"))
        fill_price = _as_float(broker.get("average_price")) or limit_price
        quantity = result.quantity if result.quantity is not None else _as_float(broker.get("quantity"))
        return result.model_copy(
            update={
                "status": status if not result.simulation else result.status,
                "order_type": str(order_type),
                "ask_price": ask_price,
                "limit_price": limit_price,
                "fill_price": fill_price if status == "filled" else result.fill_price,
                "quantity": quantity,
                "error_message": result.error_message or extract_error_message(raw),
            }
        )

    error_message = result.error_message or extract_error_message(raw)
    updates: dict[str, Any] = {
        "order_type": str(order_type),
        "ask_price": ask_price,
        "limit_price": limit_price,
        "error_message": error_message,
    }
    if error_message and result.status not in {"simulated", "submitted", "filled", "open"}:
        updates["status"] = "failed"
    return result.model_copy(update=updates)


def apply_robinhood_order_info(result: BrokerOrderResult, order_info: dict[str, Any]) -> BrokerOrderResult:
    status = map_robinhood_state(order_info.get("state"))
    fill_price = _as_float(order_info.get("average_price")) or _as_float(order_info.get("price"))
    quantity = _as_float(order_info.get("quantity")) or result.quantity
    return result.model_copy(
        update={
            "status": status,
            "fill_price": fill_price if status == "filled" else result.fill_price,
            "limit_price": result.limit_price or _as_float(order_info.get("price")),
            "quantity": quantity,
            "raw_response": {**(result.raw_response or {}), "broker_response": order_info},
        }
    )
