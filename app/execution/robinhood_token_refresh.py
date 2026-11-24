"""OAuth refresh_token exchange for Robinhood (no password / device approval).

Robinhood has no public rate-limit docs. Observed community guidance (pyhood /
pyrh): prefer refresh over password login; failed logins lock the account for
minutes; approval polling shares the same auth pool as login. Access tokens
typically last several days; refresh tokens last longer and rotate on use.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import requests

from app.execution.robinhood_auth_age import pickle_path

ROBINHOOD_CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"
OAUTH_TOKEN_URL = "https://api.robinhood.com/oauth2/token/"
# Robinhood often ignores expires_in; we still send a day-long request.
DEFAULT_EXPIRES_IN = 86400
# Written before password reauth so robin_stocks reuses device_token without
# looking like a refreshable OAuth session.
PLACEHOLDER_TOKEN = "__placeholder__"


class RobinhoodRefreshError(Exception):
    """Raised when the refresh_token grant fails."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def is_placeholder_session(data: dict[str, Any] | None) -> bool:
    """True when the pickle only exists to preserve device_token."""
    if not isinstance(data, dict):
        return True
    if data.get("placeholder") is True:
        return True
    access = str(data.get("access_token") or "")
    refresh = str(data.get("refresh_token") or "")
    bad = {PLACEHOLDER_TOKEN, "expired", ""}
    return access in bad or refresh in bad


def load_pickle_session(path: Path | None = None) -> dict[str, Any] | None:
    target = path or pickle_path()
    if not target.is_file():
        return None
    try:
        with target.open("rb") as handle:
            data = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError):
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("access_token") or not data.get("refresh_token"):
        return None
    return data


def save_pickle_session(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or pickle_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "token_type": data.get("token_type") or "Bearer",
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "device_token": data.get("device_token"),
    }
    if data.get("placeholder") is True or is_placeholder_session(payload):
        payload["placeholder"] = True
        payload["access_token"] = PLACEHOLDER_TOKEN
        payload["refresh_token"] = PLACEHOLDER_TOKEN
    with target.open("wb") as handle:
        pickle.dump(payload, handle)


def save_device_token_placeholder(device_token: str, path: Path | None = None) -> None:
    """Keep device_token on disk without a refreshable OAuth session."""
    save_pickle_session(
        {
            "token_type": "Bearer",
            "access_token": PLACEHOLDER_TOKEN,
            "refresh_token": PLACEHOLDER_TOKEN,
            "device_token": device_token,
            "placeholder": True,
        },
        path,
    )


def demote_pickle_to_device_placeholder(path: Path | None = None) -> bool:
    """After invalid_grant, keep device_token and drop dead OAuth tokens."""
    data = load_pickle_session(path)
    if data is None or not data.get("device_token"):
        return False
    if is_placeholder_session(data):
        return True
    save_device_token_placeholder(str(data["device_token"]), path)
    return True


def exchange_refresh_token(
    refresh_token: str,
    *,
    expires_in: int = DEFAULT_EXPIRES_IN,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """POST grant_type=refresh_token; returns new access/refresh tokens."""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": ROBINHOOD_CLIENT_ID,
        "scope": "internal",
        "expires_in": int(expires_in),
    }
    try:
        response = requests.post(OAUTH_TOKEN_URL, data=payload, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise RobinhoodRefreshError("robinhood_refresh_network_error", detail=str(exc)) from exc

    if response.status_code == 429:
        raise RobinhoodRefreshError("robinhood_rate_limited", detail="429 Too Many Requests")

    try:
        body = response.json()
    except ValueError:
        body = None

    if response.status_code >= 400:
        detail = None
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or body)
        raise RobinhoodRefreshError(
            "robinhood_refresh_failed",
            detail=detail or f"HTTP {response.status_code}",
        )

    if not isinstance(body, dict) or not body.get("access_token") or not body.get("refresh_token"):
        raise RobinhoodRefreshError("robinhood_refresh_failed", detail="missing tokens in response")

    return {
        "token_type": body.get("token_type") or "Bearer",
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "expires_in": body.get("expires_in"),
        "scope": body.get("scope"),
    }


def apply_tokens_to_robin_stocks(
    *,
    access_token: str,
    token_type: str,
    refresh_token: str,
    device_token: str | None,
    store_session: bool = True,
    path: Path | None = None,
) -> None:
    """Push tokens into robin_stocks globals and optionally rewrite the pickle."""
    from robin_stocks import helper

    helper.set_login_state(True)
    helper.update_session("Authorization", f"{token_type} {access_token}")
    if store_session:
        save_pickle_session(
            {
                "token_type": token_type,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "device_token": device_token,
            },
            path,
        )


def restore_session_from_pickle(path: Path | None = None) -> dict[str, Any] | None:
    """Load pickle tokens into robin_stocks without hitting the login endpoint."""
    data = load_pickle_session(path)
    if data is None or is_placeholder_session(data):
        return None
    apply_tokens_to_robin_stocks(
        access_token=str(data["access_token"]),
        token_type=str(data.get("token_type") or "Bearer"),
        refresh_token=str(data["refresh_token"]),
        device_token=str(data["device_token"]) if data.get("device_token") else None,
        store_session=False,
        path=path,
    )
    return data


def refresh_session_from_pickle(path: Path | None = None) -> dict[str, Any]:
    """Exchange the pickle refresh_token and apply the rotated pair."""
    data = load_pickle_session(path)
    if data is None:
        raise RobinhoodRefreshError("robinhood_login_required", detail="no pickle session")
    if is_placeholder_session(data):
        raise RobinhoodRefreshError(
            "robinhood_login_required",
            detail="placeholder_device_token_only",
        )

    try:
        tokens = exchange_refresh_token(str(data["refresh_token"]))
    except RobinhoodRefreshError as exc:
        detail = (exc.detail or "").lower()
        if "invalid_grant" in detail:
            # Dead refresh token: keep device_token for the next phone reauth.
            demote_pickle_to_device_placeholder(path)
            raise RobinhoodRefreshError(
                "robinhood_login_required",
                detail=exc.detail or "invalid_grant",
            ) from exc
        raise

    device_token = str(data["device_token"]) if data.get("device_token") else None
    apply_tokens_to_robin_stocks(
        access_token=str(tokens["access_token"]),
        token_type=str(tokens["token_type"]),
        refresh_token=str(tokens["refresh_token"]),
        device_token=device_token,
        store_session=True,
        path=path,
    )
    return tokens
