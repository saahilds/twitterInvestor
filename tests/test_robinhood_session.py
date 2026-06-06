import logging
from unittest.mock import MagicMock, patch

import pytest

from app.config.settings import Settings
from app.execution.robinhood_session import RobinhoodSessionManager, classify_login_error


def test_classify_429_error() -> None:
    assert classify_login_error(Exception("429 Client Error: Too Many Requests")) == "robinhood_rate_limited"


def test_classify_verification_error() -> None:
    assert (
        classify_login_error(Exception("'NoneType' object is not subscriptable"))
        == "robinhood_verification_failed"
    )


def test_login_cooldown_blocks_rapid_retries() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_login_retry_seconds=300,
    )
    manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with patch("app.execution.robinhood_session.rh") as rh_mock:
        rh_mock.login.return_value = None
        first = manager.ensure_session()
        second = manager.ensure_session()

    assert first == "robinhood_login_failed"
    assert second == "robinhood_login_failed"
    assert rh_mock.login.call_count == 1


def test_successful_login_reuses_session_without_relogin() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_session_validate_seconds=600,
    )
    manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with patch("app.execution.robinhood_session.rh") as rh_mock:
        rh_mock.login.return_value = {"access_token": "abc"}
        assert manager.ensure_session() is None
        assert manager.ensure_session() is None

    assert rh_mock.login.call_count == 1


def test_rate_limited_login_uses_longer_backoff() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_login_retry_seconds=60,
        robinhood_login_429_backoff_seconds=900,
    )
    manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with patch("app.execution.robinhood_session.rh") as rh_mock:
        rh_mock.login.side_effect = Exception("429 Too Many Requests")
        error = manager.ensure_session()
        snap = manager.snapshot()

    assert error == "robinhood_rate_limited"
    assert snap.retry_in_seconds > 800
