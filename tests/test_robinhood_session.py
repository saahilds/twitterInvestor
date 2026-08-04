import logging
import threading
import time
from unittest.mock import patch

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
    with patch("app.execution.robinhood_session.seed_auth_meta_from_pickle_mtime"):
        manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with (
        patch("app.execution.robinhood_session.rh") as rh_mock,
        patch("app.execution.robinhood_session.record_successful_auth"),
    ):
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
    with patch("app.execution.robinhood_session.seed_auth_meta_from_pickle_mtime"):
        manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with (
        patch("app.execution.robinhood_session.rh") as rh_mock,
        patch("app.execution.robinhood_session.record_successful_auth") as record,
    ):
        rh_mock.login.return_value = {"access_token": "abc"}
        assert manager.ensure_session() is None
        assert manager.ensure_session() is None

    assert rh_mock.login.call_count == 1
    record.assert_called_once()


def test_rate_limited_login_uses_longer_backoff() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_login_retry_seconds=60,
        robinhood_login_429_backoff_seconds=900,
    )
    with patch("app.execution.robinhood_session.seed_auth_meta_from_pickle_mtime"):
        manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    with patch("app.execution.robinhood_session.rh") as rh_mock:
        rh_mock.login.side_effect = Exception("429 Too Many Requests")
        error = manager.ensure_session()
        snap = manager.snapshot()

    assert error == "robinhood_rate_limited"
    assert snap.retry_in_seconds > 800


def test_reauth_single_flight_while_awaiting() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_reauth_timeout_seconds=30,
    )
    with patch("app.execution.robinhood_session.seed_auth_meta_from_pickle_mtime"):
        manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    gate = threading.Event()

    def slow_login(*_args, **_kwargs):
        gate.wait(timeout=5)
        return {"access_token": "abc"}

    with (
        patch("app.execution.robinhood_session.rh") as rh_mock,
        patch("app.execution.robinhood_session.record_successful_auth"),
        patch.object(manager, "_backup_and_clear_pickle", return_value=None),
    ):
        rh_mock.login.side_effect = slow_login
        first = manager.start_reauth()
        second = manager.start_reauth()
        assert first.status == "awaiting_approval"
        assert second.status == "awaiting_approval"
        assert first.started_at == second.started_at
        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            if manager.reauth_snapshot().status in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert manager.reauth_snapshot().status == "succeeded"


def test_reauth_timeout_marks_failed() -> None:
    settings = Settings(
        robinhood_username="user",
        robinhood_password="pass",
        robinhood_reauth_timeout_seconds=1,
    )
    with patch("app.execution.robinhood_session.seed_auth_meta_from_pickle_mtime"):
        manager = RobinhoodSessionManager(settings=settings, logger=logging.getLogger("test"))

    hang = threading.Event()

    def hang_login(*_args, **_kwargs):
        hang.wait(timeout=30)
        return {"access_token": "abc"}

    with (
        patch("app.execution.robinhood_session.rh") as rh_mock,
        patch.object(manager, "_backup_and_clear_pickle", return_value=None),
        patch.object(manager, "_restore_pickle_backup") as restore,
    ):
        rh_mock.login.side_effect = hang_login
        manager.start_reauth()
        deadline = time.time() + 5
        while time.time() < deadline:
            snap = manager.reauth_snapshot()
            if snap.status == "failed":
                break
            time.sleep(0.05)
        snap = manager.reauth_snapshot()
        assert snap.status == "failed"
        assert snap.error == "approval_timeout"
        restore.assert_called()
        hang.set()

