from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

AuthStatus = Literal["ok", "warn", "critical", "unknown", "missing"]

PICKLE_NAME = "robinhood.pickle"
META_NAME = "robinhood_auth_meta.json"


def tokens_dir() -> Path:
    return Path.home() / ".tokens"


def pickle_path() -> Path:
    return tokens_dir() / PICKLE_NAME


def meta_path() -> Path:
    return tokens_dir() / META_NAME


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def read_auth_meta() -> dict[str, Any]:
    path = meta_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_auth_meta(updates: dict[str, Any]) -> dict[str, Any]:
    path = meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_auth_meta()
    current.update(updates)
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return current


def record_successful_auth(*, moment: datetime | None = None) -> datetime:
    """Persist last successful Robinhood authentication time."""
    stamped = _as_utc(moment or datetime.now(timezone.utc))
    write_auth_meta({"last_authenticated_at": stamped.isoformat()})
    return stamped


def seed_auth_meta_from_pickle_mtime() -> datetime | None:
    """If meta is missing but pickle exists, seed last_authenticated_at from mtime."""
    meta = read_auth_meta()
    if _parse_iso(meta.get("last_authenticated_at")) is not None:
        return _parse_iso(meta.get("last_authenticated_at"))
    path = pickle_path()
    if not path.is_file():
        return None
    try:
        stamped = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    write_auth_meta({"last_authenticated_at": stamped.isoformat()})
    return stamped


def resolve_last_authenticated_at() -> datetime | None:
    meta = read_auth_meta()
    stamped = _parse_iso(meta.get("last_authenticated_at"))
    if stamped is not None:
        return stamped
    return seed_auth_meta_from_pickle_mtime()


@dataclass(slots=True, frozen=True)
class AuthAgeSnapshot:
    last_authenticated_at: datetime | None
    age_days: float | None
    max_age_days: float
    warn_days: float
    days_remaining: float | None
    refresh_due_at: datetime | None
    status: AuthStatus
    pickle_exists: bool


def compute_auth_age(
    *,
    max_age_days: float = 6.0,
    warn_days: float = 1.0,
    now: datetime | None = None,
) -> AuthAgeSnapshot:
    moment = _as_utc(now or datetime.now(timezone.utc))
    max_age = max(0.1, float(max_age_days))
    warn = max(0.0, float(warn_days))
    exists = pickle_path().is_file()
    last = resolve_last_authenticated_at()

    if last is None:
        status: AuthStatus = "missing" if not exists else "unknown"
        return AuthAgeSnapshot(
            last_authenticated_at=None,
            age_days=None,
            max_age_days=max_age,
            warn_days=warn,
            days_remaining=None,
            refresh_due_at=None,
            status=status,
            pickle_exists=exists,
        )

    age_days = max(0.0, (moment - last).total_seconds() / 86400.0)
    days_remaining = max_age - age_days
    refresh_due_at = last + timedelta(days=max_age)

    if days_remaining <= 0:
        status = "critical"
    elif days_remaining <= warn:
        status = "warn"
    else:
        status = "ok"

    return AuthAgeSnapshot(
        last_authenticated_at=last,
        age_days=round(age_days, 3),
        max_age_days=max_age,
        warn_days=warn,
        days_remaining=round(days_remaining, 3),
        refresh_due_at=refresh_due_at,
        status=status,
        pickle_exists=exists,
    )


def should_alert_auth_status(status: AuthStatus) -> bool:
    if status not in {"warn", "critical"}:
        return False
    meta = read_auth_meta()
    return meta.get("last_alert_status") != status


def mark_auth_alert_sent(status: AuthStatus) -> None:
    write_auth_meta({"last_alert_status": status})
