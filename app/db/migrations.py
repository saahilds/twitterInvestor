from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def migrate_trades_table(engine: Engine) -> None:
    """Add trade tracking columns to existing SQLite databases."""
    inspector = inspect(engine)
    if "trades" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("trades")}
    additions: list[tuple[str, str]] = [
        ("source_tweet_id", "VARCHAR(64)"),
        ("order_type", "VARCHAR(32)"),
        ("ask_price", "REAL"),
        ("limit_price", "REAL"),
        ("fill_price", "REAL"),
        ("error_message", "VARCHAR(512)"),
        ("account_number", "VARCHAR(64)"),
        ("updated_at", "DATETIME"),
    ]

    with engine.begin() as connection:
        for column_name, column_type in additions:
            if column_name in existing:
                continue
            connection.execute(text(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}"))
            existing.add(column_name)

        if "updated_at" in existing:
            connection.execute(text("UPDATE trades SET updated_at = created_at WHERE updated_at IS NULL"))
