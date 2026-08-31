from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import Settings
from app.execution.robinhood_auth_age import (
    pickle_path,
    record_successful_auth,
    seed_auth_meta_from_pickle_mtime,
)

try:
    from robin_stocks import robinhood as rh
except Exception:  # pragma: no cover - environment-specific
    rh = None

try:
    import pyotp
except Exception:  # pragma: no cover - environment-specific
    pyotp = None


@dataclass(slots=True)
class SessionSnapshot:
    logged_in: bool
    last_error: str | None
    blocked_until_monotonic: float
    consecutive_failures: int
    last_success_monotonic: float | None

    @property
    def retry_in_seconds(self) -> int:
        return max(0, int(self.blocked_until_monotonic - time.monotonic()))


@dataclass(slots=True)
class ReauthJobSnapshot:
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    message: str | None


@dataclass
class _SessionState:
    logged_in: bool = False
    last_error: str | None = None
    blocked_until: float = 0.0
    last_success_at: float | None = None
    last_validated_at: float | None = None
    consecutive_failures: int = 0
    cooldown_logged_at: float = 0.0


@dataclass
class _ReauthState:
    status: str = "idle"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    message: str | None = None


class RobinhoodSessionManager:
    """Single-flight Robinhood login with cooldowns after failures / 429s."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        alert_service: object | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.alert_service = alert_service
        self._lock = threading.RLock()
        self._state = _SessionState()
        self._reauth = _ReauthState()
        self._reauth_thread: threading.Thread | None = None
        seed_auth_meta_from_pickle_mtime()

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return SessionSnapshot(
                logged_in=self._state.logged_in,
                last_error=self._state.last_error,
                blocked_until_monotonic=self._state.blocked_until,
                consecutive_failures=self._state.consecutive_failures,
                last_success_monotonic=self._state.last_success_at,
            )

    def reauth_snapshot(self) -> ReauthJobSnapshot:
        with self._lock:
            return ReauthJobSnapshot(
                status=self._reauth.status,
                started_at=self._reauth.started_at,
                finished_at=self._reauth.finished_at,
                error=self._reauth.error,
                message=self._reauth.message,
            )

    def invalidate(self, *, reason: str | None = None) -> None:
        with self._lock:
            self._state.logged_in = False
            self._state.last_validated_at = None
            if reason:
                self._state.last_error = reason

    def start_reauth(self) -> ReauthJobSnapshot:
        """Start a background force-login that waits for phone approval."""
        with self._lock:
            if self._reauth.status == "awaiting_approval":
                return self.reauth_snapshot()

            if rh is None:
                self._set_reauth_failed("robin_stocks_unavailable", "robin_stocks unavailable")
                return self.reauth_snapshot()

            if not self.settings.robinhood_username or not self.settings.robinhood_password:
                self._set_reauth_failed(
                    "missing_robinhood_credentials",
                    "Robinhood credentials are not configured",
                )
                return self.reauth_snapshot()

            self._reauth.status = "awaiting_approval"
            self._reauth.started_at = datetime.now(timezone.utc)
            self._reauth.finished_at = None
            self._reauth.error = None
            self._reauth.message = "Approve the login request in the Robinhood app now."
            thread = threading.Thread(
                target=self._run_reauth_job,
                name="robinhood-reauth",
                daemon=True,
            )
            self._reauth_thread = thread
            thread.start()
            return self.reauth_snapshot()

    def ensure_session(self, *, force: bool = False) -> str | None:
        """Return an error code when no session is available, else ``None``."""
        if rh is None:
            return "robin_stocks_unavailable"

        if not self.settings.robinhood_username or not self.settings.robinhood_password:
            return "missing_robinhood_credentials"

        with self._lock:
            now = time.monotonic()
            if not force and self._state.blocked_until > now:
                self._log_cooldown(now)
                return self._state.last_error or "robinhood_login_cooldown"

            if not force and self._state.logged_in:
                if self._session_recently_validated(now) or self._validate_existing_session(now):
                    return None

        # Login can block on phone approval — do not hold the lock.
        ok, error = self._perform_login()
        finished = time.monotonic()
        with self._lock:
            if ok:
                self._mark_login_success(finished)
                return None

            self._state.logged_in = False
            self._state.last_validated_at = None
            self._state.consecutive_failures += 1
            self._state.last_error = error or "robinhood_login_failed"
            backoff = self._backoff_seconds(error, self._state.consecutive_failures)
            self._state.blocked_until = finished + backoff
            self.logger.warning(
                "robinhood_login_failed",
                extra={
                    "event_type": "robinhood_auth",
                    "error": self._state.last_error,
                    "backoff_seconds": backoff,
                    "consecutive_failures": self._state.consecutive_failures,
                },
            )
            if self.alert_service is not None and getattr(
                self.alert_service, "on_worker_errors", True
            ):
                send_sync = getattr(self.alert_service, "send_sync", None)
                if callable(send_sync):
                    send_sync(
                        "robinhood_auth_failed",
                        {"error": self._state.last_error},
                        key=self._state.last_error or "auth",
                    )
            return self._state.last_error

    def _run_reauth_job(self) -> None:
        backup = self._backup_and_clear_pickle()
        timeout = max(1, int(self.settings.robinhood_reauth_timeout_seconds))
        finished: dict[str, object] = {}

        def worker() -> None:
            try:
                self.invalidate(reason="reauth_in_progress")
                error = self.ensure_session(force=True)
                finished["error"] = error
            except Exception as exc:  # pragma: no cover - defensive
                finished["error"] = classify_login_error(exc)

        thread = threading.Thread(target=worker, name="robinhood-reauth-login", daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            with self._lock:
                self._reauth.status = "failed"
                self._reauth.finished_at = datetime.now(timezone.utc)
                self._reauth.error = "approval_timeout"
                self._reauth.message = (
                    f"No approval within {timeout}s. Open the Robinhood app and try again."
                )
            self._restore_pickle_backup(backup)
            self.logger.warning(
                "robinhood_reauth_timeout",
                extra={"event_type": "robinhood_auth", "timeout_seconds": timeout},
            )
            return

        error = finished.get("error")
        if error is None:
            with self._lock:
                self._reauth.status = "succeeded"
                self._reauth.finished_at = datetime.now(timezone.utc)
                self._reauth.error = None
                self._reauth.message = "Robinhood auth refreshed."
            if backup is not None and backup.exists():
                try:
                    backup.unlink()
                except OSError:
                    pass
            self.logger.info("robinhood_reauth_succeeded", extra={"event_type": "robinhood_auth"})
            return

        with self._lock:
            self._reauth.status = "failed"
            self._reauth.finished_at = datetime.now(timezone.utc)
            self._reauth.error = str(error)
            self._reauth.message = f"Reauth failed: {error}"
        self._restore_pickle_backup(backup)
        self.logger.warning(
            "robinhood_reauth_failed",
            extra={"event_type": "robinhood_auth", "error": str(error)},
        )

    def _backup_and_clear_pickle(self) -> Path | None:
        path = pickle_path()
        if not path.is_file():
            return None
        backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
        try:
            shutil.move(str(path), str(backup))
            return backup
        except OSError as exc:
            self.logger.warning(
                "robinhood_pickle_backup_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )
            return None

    def _restore_pickle_backup(self, backup: Path | None) -> None:
        if backup is None or not backup.is_file():
            return
        path = pickle_path()
        try:
            if path.exists():
                path.unlink()
            shutil.move(str(backup), str(path))
        except OSError as exc:
            self.logger.warning(
                "robinhood_pickle_restore_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )

    def _set_reauth_failed(self, error: str, message: str) -> None:
        self._reauth.status = "failed"
        self._reauth.started_at = datetime.now(timezone.utc)
        self._reauth.finished_at = self._reauth.started_at
        self._reauth.error = error
        self._reauth.message = message

    def _mark_login_success(self, finished: float) -> None:
        self._state.logged_in = True
        self._state.last_error = None
        self._state.consecutive_failures = 0
        self._state.last_success_at = finished
        self._state.last_validated_at = finished
        self._state.blocked_until = 0.0
        record_successful_auth()
        self.logger.info(
            "robinhood_login_success",
            extra={"event_type": "robinhood_auth"},
        )

    def _perform_login(self) -> tuple[bool, str | None]:
        mfa_code = None
        if self.settings.robinhood_mfa_secret:
            if pyotp is None:
                return False, "pyotp_unavailable_for_mfa"
            mfa_code = pyotp.TOTP(self.settings.robinhood_mfa_secret).now()

        try:
            result = rh.login(
                username=self.settings.robinhood_username,
                password=self.settings.robinhood_password,
                mfa_code=mfa_code,
                expiresIn=86400,
            )
        except Exception as exc:
            return False, classify_login_error(exc)

        if result:
            return True, None
        return False, "robinhood_login_failed"

    def _session_recently_validated(self, now: float) -> bool:
        validated_at = self._state.last_validated_at
        if validated_at is None:
            return False
        return (now - validated_at) <= self.settings.robinhood_session_validate_seconds

    def _validate_existing_session(self, now: float) -> bool:
        try:
            profile = rh.profiles.load_account_profile()
        except Exception as exc:
            self.logger.debug(
                "robinhood_session_validation_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )
            return False

        if profile is None or profile is False:
            return False
        if isinstance(profile, list) and not profile:
            return False

        self._state.last_validated_at = now
        return True

    def _backoff_seconds(self, error: str | None, failures: int) -> float:
        if error == "robinhood_rate_limited":
            return float(self.settings.robinhood_login_429_backoff_seconds)
        base = float(self.settings.robinhood_login_retry_seconds)
        return min(base * max(1, failures), base * 4)

    def _log_cooldown(self, now: float) -> None:
        if now - self._state.cooldown_logged_at < 60:
            return
        self._state.cooldown_logged_at = now
        retry_in = max(0, int(self._state.blocked_until - now))
        self.logger.warning(
            "robinhood_login_skipped_cooldown",
            extra={
                "event_type": "robinhood_auth",
                "error": self._state.last_error,
                "retry_in_seconds": retry_in,
            },
        )


def classify_login_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "429" in message or "too many requests" in message:
        return "robinhood_rate_limited"
    if "nonetype" in message and "subscriptable" in message:
        return "robinhood_verification_failed"
    if "verification" in message or "challenge" in message:
        return "robinhood_verification_required"
    return "robinhood_login_failed"
