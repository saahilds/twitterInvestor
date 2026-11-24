import logging
from unittest.mock import patch

import pytest

from app.config.settings import Settings
from app.execution.robinhood_device_approval import (
    RobinhoodRateLimitedError,
    patch_device_approval,
    validate_sheriff_id,
)
from app.execution.robinhood_session import RobinhoodSessionManager, classify_login_error


def test_classify_nonetype_as_rate_limited() -> None:
    assert (
        classify_login_error(Exception("'NoneType' object is not subscriptable"))
        == "robinhood_rate_limited"
    )


def test_classify_rate_limited_error_type() -> None:
    assert (
        classify_login_error(RobinhoodRateLimitedError("429 Too Many Requests"))
        == "robinhood_rate_limited"
    )


def test_validate_sheriff_raises_on_repeated_empty_prompt_polls() -> None:
    machine = {"id": "machine-1"}
    inquiry = {
        "context": {
            "sheriff_challenge": {
                "type": "prompt",
                "status": "issued",
                "id": "challenge-1",
            }
        }
    }

    with (
        patch(
            "robin_stocks.robinhood.helper.request_post",
            return_value=machine,
        ),
        patch(
            "robin_stocks.robinhood.helper.request_get",
            side_effect=[inquiry, None, None, None, None, None, None, None, None, None],
        ),
        patch("app.execution.robinhood_device_approval.time.sleep"),
        patch(
            "app.execution.robinhood_device_approval.time.monotonic",
            side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 200],
        ),
    ):
        with pytest.raises(RobinhoodRateLimitedError):
            validate_sheriff_id(
                "device",
                "workflow",
                timeout_seconds=60,
                poll_seconds=0.01,
                max_empty_polls=3,
                skip_status_poll=False,
            )


def test_user_confirm_signal_requires_prompt_ready() -> None:
    from app.execution import robinhood_device_approval as dap

    dap.clear_approval_signals()
    assert dap.signal_approval_confirmed() is False
    dap._prompt_ready.set()
    assert dap.signal_approval_confirmed() is True
    assert dap.is_approval_confirmed() is True


def test_perform_login_uses_device_approval_patch() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_reauth_timeout_seconds=45,
    )
    with patch("app.execution.robinhood_session.seed_auth_meta_from_pickle_mtime"):
        manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with (
        patch("app.execution.robinhood_session.rh") as rh_mock,
        patch("app.execution.robinhood_session.patch_device_approval") as patch_fn,
        patch("app.execution.robinhood_session.record_successful_auth"),
    ):
        patch_fn.return_value.__enter__.return_value = None
        patch_fn.return_value.__exit__.return_value = None
        rh_mock.login.return_value = {"access_token": "abc"}
        ok, err = manager._perform_login()

    assert ok is True
    assert err is None
    patch_fn.assert_called_once()
    assert patch_fn.call_args.kwargs["timeout_seconds"] == 45


def test_patch_device_approval_restores_original() -> None:
    import robin_stocks.robinhood.authentication as auth_mod

    original = auth_mod._validate_sherrif_id
    with patch_device_approval(timeout_seconds=30):
        assert auth_mod._validate_sherrif_id is not original
    assert auth_mod._validate_sherrif_id is original
