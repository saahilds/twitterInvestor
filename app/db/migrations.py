from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.config.account_managers import DEFAULT_MANAGER_ID, legacy_manager_id, parse_bot_managers
from app.config.settings import get_settings


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
        ("manager_id", "VARCHAR(32)"),
    ]

    with engine.begin() as connection:
        for column_name, column_type in additions:
            if column_name in existing:
                continue
            connection.execute(text(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}"))
            existing.add(column_name)

        if "updated_at" in existing:
            connection.execute(text("UPDATE trades SET updated_at = created_at WHERE updated_at IS NULL"))
        if "manager_id" in existing:
            backfill_manager = legacy_manager_id(get_settings())
            connection.execute(
                text("UPDATE trades SET manager_id = :manager_id WHERE manager_id IS NULL"),
                {"manager_id": backfill_manager},
            )


def migrate_parsed_signals_table(engine: Engine) -> None:
    inspector = inspect(engine)
    if "parsed_signals" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("parsed_signals")}
    if "manager_id" in existing:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE parsed_signals ADD COLUMN manager_id VARCHAR(32)"))
        backfill_manager = legacy_manager_id(get_settings())
        connection.execute(
            text("UPDATE parsed_signals SET manager_id = :manager_id WHERE manager_id IS NULL"),
            {"manager_id": backfill_manager},
        )


def migrate_recognized_tickers_table(engine: Engine) -> None:
    inspector = inspect(engine)
    if "recognized_tickers" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("recognized_tickers")}
    if "manager_id" in columns:
        return

    settings = get_settings()
    manager_ids = [cfg.id for cfg in parse_bot_managers(settings)]
    backfill_manager = legacy_manager_id(settings)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE recognized_tickers_new (
                    manager_id VARCHAR(32) NOT NULL,
                    ticker VARCHAR(16) NOT NULL,
                    first_seen_at DATETIME,
                    source_tweet_id VARCHAR(64),
                    PRIMARY KEY (manager_id, ticker)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO recognized_tickers_new (manager_id, ticker, first_seen_at, source_tweet_id)
                SELECT :manager_id, ticker, first_seen_at, source_tweet_id
                FROM recognized_tickers
                """
            ),
            {"manager_id": backfill_manager},
        )
        for manager_id in manager_ids:
            if manager_id == backfill_manager:
                continue
            connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO recognized_tickers_new (manager_id, ticker, first_seen_at, source_tweet_id)
                    SELECT :manager_id, ticker, first_seen_at, source_tweet_id
                    FROM recognized_tickers
                    """
                ),
                {"manager_id": manager_id},
            )
        connection.execute(text("DROP TABLE recognized_tickers"))
        connection.execute(text("ALTER TABLE recognized_tickers_new RENAME TO recognized_tickers"))


def run_migrations(engine: Engine) -> None:
    migrate_trades_table(engine)
    migrate_parsed_signals_table(engine)
    migrate_recognized_tickers_table(engine)
