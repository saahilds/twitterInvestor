from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.execution import robinhood_token_refresh as refresh


def test_load_and_save_pickle_session(tmp_path: Path) -> None:
    path = tmp_path / "robinhood.pickle"
    refresh.save_pickle_session(
        {
            "token_type": "Bearer",
            "access_token": "access",
            "refresh_token": "refresh",
            "device_token": "device",
        },
        path,
    )
    loaded = refresh.load_pickle_session(path)
    assert loaded is not None
    assert loaded["access_token"] == "access"
    assert loaded["refresh_token"] == "refresh"
    assert loaded["device_token"] == "device"


def test_exchange_refresh_token_success() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "token_type": "Bearer",
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 86400,
    }
    with patch("app.execution.robinhood_token_refresh.requests.post", return_value=response) as post:
        tokens = refresh.exchange_refresh_token("old-refresh")

    assert tokens["access_token"] == "new-access"
    assert tokens["refresh_token"] == "new-refresh"
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"
    assert post.call_args.kwargs["data"]["refresh_token"] == "old-refresh"
    assert post.call_args.kwargs["data"]["client_id"] == refresh.ROBINHOOD_CLIENT_ID


def test_exchange_refresh_token_rate_limited() -> None:
    response = MagicMock()
    response.status_code = 429
    response.json.return_value = {"detail": "Too Many Requests"}
    with patch("app.execution.robinhood_token_refresh.requests.post", return_value=response):
        with pytest.raises(refresh.RobinhoodRefreshError) as exc:
            refresh.exchange_refresh_token("old-refresh")
    assert exc.value.code == "robinhood_rate_limited"


def test_refresh_session_from_pickle_rotates_tokens(tmp_path: Path) -> None:
    path = tmp_path / "robinhood.pickle"
    refresh.save_pickle_session(
        {
            "token_type": "Bearer",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "device_token": "device-1",
        },
        path,
    )
    with (
        patch(
            "app.execution.robinhood_token_refresh.exchange_refresh_token",
            return_value={
                "token_type": "Bearer",
                "access_token": "new-access",
                "refresh_token": "new-refresh",
            },
        ),
        patch("robin_stocks.helper.set_login_state"),
        patch("robin_stocks.helper.update_session"),
    ):
        tokens = refresh.refresh_session_from_pickle(path)

    assert tokens["access_token"] == "new-access"
    loaded = refresh.load_pickle_session(path)
    assert loaded is not None
    assert loaded["access_token"] == "new-access"
    assert loaded["refresh_token"] == "new-refresh"
    assert loaded["device_token"] == "device-1"
