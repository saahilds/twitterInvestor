from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.db_models import ParsedSignal, Trade


def repair_manager_ids(
    session_factory: Callable[[], Session],
    *,
    manager_to_account: dict[str, str],
    legacy_manager: str | None = None,
) -> int:
    """Reassign manager_id on historical rows using Robinhood account_number."""
    account_to_manager = {
        str(account_number): manager_id
        for manager_id, account_number in manager_to_account.items()
        if account_number
    }
    if not account_to_manager and not legacy_manager:
        return 0

    updated = 0
    with session_factory() as db:
        for account_number, manager_id in account_to_manager.items():
            result = db.execute(
                update(Trade)
                .where(Trade.account_number == account_number)
                .where(Trade.manager_id != manager_id)
                .values(manager_id=manager_id)
            )
            updated += result.rowcount or 0

        if legacy_manager:
            result = db.execute(
                update(Trade)
                .where(or_(Trade.account_number.is_(None), Trade.account_number == ""))
                .where(Trade.manager_id != legacy_manager)
                .values(manager_id=legacy_manager)
            )
            updated += result.rowcount or 0

        trade_rows = db.execute(select(Trade.id, Trade.parsed_signal_id, Trade.manager_id)).all()
        for trade_id, parsed_signal_id, manager_id in trade_rows:
            if parsed_signal_id is None:
                continue
            result = db.execute(
                update(ParsedSignal)
                .where(ParsedSignal.id == parsed_signal_id)
                .where(ParsedSignal.manager_id != manager_id)
                .values(manager_id=manager_id)
            )
            updated += result.rowcount or 0

        if legacy_manager:
            linked_ids = {
                parsed_signal_id for _, parsed_signal_id, _ in trade_rows if parsed_signal_id is not None
            }
            stmt = (
                update(ParsedSignal)
                .where(ParsedSignal.manager_id != legacy_manager)
                .values(manager_id=legacy_manager)
            )
            if linked_ids:
                stmt = stmt.where(ParsedSignal.id.not_in(linked_ids))
            result = db.execute(stmt)
            updated += result.rowcount or 0

        db.commit()
    return updated
