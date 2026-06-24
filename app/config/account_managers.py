from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings
from app.execution.robinhood_accounts import load_all_accounts, resolve_account_number

DEFAULT_MANAGER_ID = "individual"

_DISABLED_FLAGS = {"off", "disabled", "false", "0"}


def legacy_manager_id(settings: Settings) -> str:
    """Manager id used for pre-multi-account data and migration backfill."""
    if settings.robinhood_account and settings.robinhood_account.strip():
        return settings.robinhood_account.strip().lower()
    return DEFAULT_MANAGER_ID


def default_manager_id(settings: Settings, configs: list[AccountManagerConfig]) -> str:
    """Default dashboard/API manager when ?manager= is omitted."""
    ids = [cfg.id for cfg in configs]
    preferred = legacy_manager_id(settings)
    if preferred in ids:
        return preferred
    if ids:
        return ids[0]
    return DEFAULT_MANAGER_ID


@dataclass(slots=True)
class AccountManagerConfig:
    id: str
    robinhood_account: str
    enabled: bool = True


def _parse_manager_entry(part: str) -> tuple[str, bool | None]:
    """Return manager id and optional explicit enabled flag."""
    if ":" in part:
        manager_id, flag = part.rsplit(":", 1)
        enabled = flag.strip().lower() not in _DISABLED_FLAGS
        return manager_id.strip().lower(), enabled
    return part.strip().lower(), None


def _default_enabled(manager_id: str, settings: Settings, manager_ids: list[str]) -> bool:
    if settings.bot_managers_enable_all:
        return True
    if len(manager_ids) <= 1:
        return True
    primary = (settings.robinhood_account or "").strip().lower()
    if not primary:
        return True
    return manager_id == primary


def parse_bot_managers(settings: Settings) -> list[AccountManagerConfig]:
    raw = settings.bot_managers
    if raw:
        parts = [part.strip() for part in raw.split(",") if part.strip()]
    elif settings.robinhood_account:
        parts = [settings.robinhood_account.strip()]
    else:
        parts = [DEFAULT_MANAGER_ID]

    seen: set[str] = set()
    parsed_entries: list[tuple[str, bool | None]] = []
    for part in parts:
        manager_id, explicit_enabled = _parse_manager_entry(part)
        if manager_id in seen:
            raise ValueError(f"duplicate_bot_manager:{manager_id}")
        seen.add(manager_id)
        parsed_entries.append((manager_id, explicit_enabled))

    manager_ids = [manager_id for manager_id, _ in parsed_entries]
    configs: list[AccountManagerConfig] = []
    for manager_id, explicit_enabled in parsed_entries:
        if explicit_enabled is None:
            enabled = _default_enabled(manager_id, settings, manager_ids)
        else:
            enabled = explicit_enabled
        configs.append(
            AccountManagerConfig(
                id=manager_id,
                robinhood_account=manager_id,
                enabled=enabled,
            )
        )
    return configs


def validate_manager_accounts(
    configs: list[AccountManagerConfig],
    *,
    accounts: list[dict] | None = None,
) -> dict[str, str]:
    """Resolve each manager id to a Robinhood account_number. Raises on failure."""
    rows = accounts if accounts is not None else load_all_accounts()
    resolved: dict[str, str] = {}
    for cfg in configs:
        account_number = resolve_account_number(cfg.robinhood_account, rows)
        resolved[cfg.id] = account_number
    return resolved
