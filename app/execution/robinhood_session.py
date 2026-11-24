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
    clear_login_block,
    clear_session_alert,
    pickle_path,
    read_login_block,
    record_successful_auth,
    seed_auth_meta_from_pickle_mtime,
    set_login_block,
)
from app.execution.robinhood_device_approval import (
    RobinhoodRateLimitedError,
    RobinhoodVerificationTimeout,
    is_prompt_ready,
    patch_device_approval,
    signal_approval_confirmed,
    take_last_error,
)
from app.execution.robinhood_token_refresh import (
    RobinhoodRefreshError,
    load_pickle_session,
    refresh_session_from_pickle,
    restore_session_from_pickle,
    save_pickle_session,
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

            # Explicit user action clears persisted cool-downs.
            clear_login_block()
            self._state.blocked_until = 0.0
            self._state.consecutive_failures = 0
            self._state.last_error = None

            self._reauth.status = "awaiting_approval"
            self._reauth.started_at = datetime.now(timezone.utc)
            self._reauth.finished_at = None
            self._reauth.error = None
            self._reauth.message = (
                "Approve in the Robinhood app, then confirm via "
                "POST /robinhood/reauth/confirm-approval."
            )
            thread = threading.Thread(
                target=self._run_reauth_job,
                name="robinhood-reauth",
                daemon=True,
            )
            self._reauth_thread = thread
            thread.start()
            return self.reauth_snapshot()

    def confirm_reauth_approval(self) -> ReauthJobSnapshot:
        """Operator asserts the phone prompt was approved; skip status polling."""
        with self._lock:
            if self._reauth.status != "awaiting_approval":
                self._reauth.message = (
                    f"No reauth awaiting approval (status={self._reauth.status})."
                )
                return self.reauth_snapshot()

        if not is_prompt_ready():
            with self._lock:
                self._reauth.message = (
                    "Prompt not issued yet — wait for the Robinhood app notification, "
                    "approve it, then confirm again."
                )
            return self.reauth_snapshot()

        if signal_approval_confirmed():
            with self._lock:
                self._reauth.message = (
                    "Approval recorded — finishing login without get_prompts_status."
                )
        return self.reauth_snapshot()

    def ensure_session(self, *, force: bool = False) -> str | None:
        """Return an error code when no session is available, else ``None``.

        Non-force path never hits password + device approval. It restores the
        pickle, exchanges ``refresh_token`` when needed, then stops. Only
        ``force=True`` (dashboard reauth) may start a full login.
        """
        if rh is None:
            return "robin_stocks_unavailable"

        if not self.settings.robinhood_username or not self.settings.robinhood_password:
            return "missing_robinhood_credentials"

        with self._lock:
            now = time.monotonic()
            if not force and self._state.blocked_until > now:
                self._log_cooldown(now)
                return self._state.last_error or "robinhood_login_cooldown"

            if not force:
                blocked_until, blocked_reason = read_login_block()
                if blocked_until is not None:
                    self._state.last_error = blocked_reason or "robinhood_rate_limited"
                    self._state.blocked_until = max(
                        self._state.blocked_until,
                        now + max(0.0, (blocked_until - datetime.now(timezone.utc)).total_seconds()),
                    )
                    self._log_cooldown(now)
                    return self._state.last_error

            # No saved session: never auto-trigger device approval. Only explicit
            # dashboard /robinhood/reauth (force=True) may start a fresh login.
            if not force and not pickle_path().is_file():
                self._state.last_error = "robinhood_login_required"
                self.logger.warning(
                    "robinhood_login_skipped_no_pickle",
                    extra={"event_type": "robinhood_auth"},
                )
                return "robinhood_login_required"

            if not force and self._state.logged_in:
                if self._session_recently_validated(now) or self._validate_existing_session(now):
                    return None

        if not force:
            # Silent path: pickle restore → OAuth refresh. Never password login.
            # Network I/O runs outside the lock.
            return self._ensure_session_via_refresh()

        # force=True: password + device approval (can block — do not hold the lock).
        ok, error = self._perform_login()
        finished = time.monotonic()
        with self._lock:
            if ok:
                self._mark_login_success(finished)
                return None
            return self._record_login_failure(error or "robinhood_login_failed", finished)
    def _run_reauth_job(self) -> None:
        backup = self._backup_and_clear_pickle()
        # Keep a stable device_token so RH doesn't treat every reauth as a new device.
        self._seed_device_token_pickle(sources=[backup, self._stale_pickle_path()])
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
        # Approval polling may sleep after a late 429; keep a buffer on long timeouts.
        join_budget = timeout + (180 if timeout >= 60 else 5)
        thread.join(timeout=join_budget)

        if thread.is_alive():
            with self._lock:
                self._reauth.status = "failed"
                self._reauth.finished_at = datetime.now(timezone.utc)
                self._reauth.error = "approval_timeout"
                self._reauth.message = (
                    f"No approval within {timeout}s. Open the Robinhood app and try again."
                )
            # User may still approve late; keep the prior session if it was usable.
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

        error_s = str(error)
        with self._lock:
            self._reauth.status = "failed"
            self._reauth.finished_at = datetime.now(timezone.utc)
            self._reauth.error = error_s
            if error_s == "robinhood_rate_limited":
                hours = max(
                    1,
                    int(round(self.settings.robinhood_login_429_backoff_seconds / 3600)),
                )
                self._reauth.message = (
                    f"Robinhood rate-limited device approval. Wait ~{hours}h, "
                    "then Refresh RH auth once with the app ready to approve."
                )
            else:
                self._reauth.message = f"Reauth failed: {error_s}"
        # Rate-limit / verification failures mean the old pickle was already dead;
        # restoring it only restarts the 429 loop.
        if error_s in {"robinhood_rate_limited", "robinhood_verification_required"}:
            if backup is not None and backup.exists():
                try:
                    backup.unlink()
                except OSError:
                    pass
        else:
            self._restore_pickle_backup(backup)
        self.logger.warning(
            "robinhood_reauth_failed",
            extra={"event_type": "robinhood_auth", "error": error_s},
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

    @staticmethod
    def _stale_pickle_path() -> Path:
        return pickle_path().with_name("robinhood.pickle.stale.1795109059")

    def _seed_device_token_pickle(self, *, sources: list[Path | None]) -> bool:
        """Write a placeholder pickle so robin_stocks reuses device_token on login."""
        device_token: str | None = None
        for source in sources:
            if source is None or not source.is_file():
                continue
            try:
                data = load_pickle_session(source)
            except Exception:
                data = None
            if data and data.get("device_token"):
                device_token = str(data["device_token"])
                break
            # load_pickle_session requires access+refresh; fall back to raw pickle.
            try:
                import pickle as _pickle

                with source.open("rb") as handle:
                    raw = _pickle.load(handle)
                if isinstance(raw, dict) and raw.get("device_token"):
                    device_token = str(raw["device_token"])
                    break
            except Exception:
                continue

        if not device_token:
            self.logger.info(
                "robinhood_device_token_seed_skipped",
                extra={"event_type": "robinhood_auth"},
            )
            return False

        try:
            # Expired placeholders force password login, but keep device_token.
            save_pickle_session(
                {
                    "token_type": "Bearer",
                    "access_token": "expired",
                    "refresh_token": "expired",
                    "device_token": device_token,
                },
                path=pickle_path(),
            )
        except OSError as exc:
            self.logger.warning(
                "robinhood_device_token_seed_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )
            return False

        self.logger.info(
            "robinhood_device_token_seeded",
            extra={"event_type": "robinhood_auth"},
        )
        return True

    def _restore_pickle_backup(self, backup: Path | None) -> None:
        if backup is None or not backup.is_file():
            return
        # Do not put a stale pickle back — it just forces another device-approval
        # loop and more 429s on get_prompts_status.
        max_age_days = float(self.settings.robinhood_pickle_max_age_days)
        try:
            age_days = (time.time() - backup.stat().st_mtime) / 86400.0
        except OSError:
            age_days = max_age_days + 1
        if age_days > max_age_days:
            self.logger.warning(
                "robinhood_pickle_backup_expired_discarded",
                extra={
                    "event_type": "robinhood_auth",
                    "age_days": round(age_days, 3),
                    "max_age_days": max_age_days,
                },
            )
            try:
                backup.unlink()
            except OSError:
                pass
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
        clear_session_alert()
        self.logger.info(
            "robinhood_login_success",
            extra={"event_type": "robinhood_auth"},
        )

    def _ensure_session_via_refresh(self) -> str | None:
        """Restore pickle / refresh_token without password or device approval."""
        # 1) Re-apply pickle tokens and validate.
        try:
            restored = restore_session_from_pickle()
        except Exception as exc:
            restored = None
            self.logger.debug(
                "robinhood_pickle_restore_failed",
                extra={"event_type": "robinhood_auth", "error": str(exc)},
            )
        if restored is not None:
            now = time.monotonic()
            with self._lock:
                if self._validate_existing_session(now):
                    self._mark_login_success(now)
                    self.logger.info(
                        "robinhood_session_restored_from_pickle",
                        extra={"event_type": "robinhood_auth"},
                    )
                    return None

        # 2) Exchange refresh_token (no phone). Rotates tokens in the pickle.
        try:
            refresh_session_from_pickle()
        except RobinhoodRefreshError as exc:
            error = exc.code if exc.code.startswith("robinhood_") else "robinhood_refresh_failed"
            with self._lock:
                return self._record_login_failure(error, time.monotonic(), alert=False)
        except Exception as exc:
            with self._lock:
                return self._record_login_failure(
                    classify_login_error(exc),
                    time.monotonic(),
                    alert=False,
                )

        now = time.monotonic()
        with self._lock:
            if self._validate_existing_session(now):
                self._mark_login_success(now)
                self.logger.info(
                    "robinhood_session_refreshed",
                    extra={"event_type": "robinhood_auth"},
                )
                return None
            return self._record_login_failure(
                "robinhood_login_required",
                now,
                alert=False,
            )

    def _record_login_failure(
        self,
        error: str,
        finished: float,
        *,
        alert: bool = True,
    ) -> str:
        self._state.logged_in = False
        self._state.last_validated_at = None
        self._state.consecutive_failures += 1
        self._state.last_error = error
        backoff = self._backoff_seconds(error, self._state.consecutive_failures)
        self._state.blocked_until = finished + backoff
        if error == "robinhood_rate_limited":
            set_login_block(
                until=datetime.fromtimestamp(time.time() + backoff, tz=timezone.utc),
                reason="robinhood_rate_limited",
            )
        self.logger.warning(
            "robinhood_login_failed",
            extra={
                "event_type": "robinhood_auth",
                "error": error,
                "backoff_seconds": backoff,
                "consecutive_failures": self._state.consecutive_failures,
            },
        )
        # Password-login failures only: logged-out / refresh misses use the
        # worker's low-noise session alert instead of spamming auth_failed.
        if alert and self.alert_service is not None and getattr(
            self.alert_service, "on_worker_errors", True
        ):
            send_sync = getattr(self.alert_service, "send_sync", None)
            if callable(send_sync):
                send_sync(
                    "robinhood_auth_failed",
                    {"error": error},
                    key=error or "auth",
                )
        return error

    def _perform_login(self) -> tuple[bool, str | None]:
        mfa_code = None
        if self.settings.robinhood_mfa_secret:
            if pyotp is None:
                return False, "pyotp_unavailable_for_mfa"
            mfa_code = pyotp.TOTP(self.settings.robinhood_mfa_secret).now()

        timeout = max(30, int(self.settings.robinhood_reauth_timeout_seconds))
        poll_seconds = max(5.0, float(self.settings.robinhood_reauth_poll_seconds))
        max_empty = max(0, int(self.settings.robinhood_reauth_max_empty_polls))
        skip_status = bool(self.settings.robinhood_reauth_skip_status_poll)
        try:
            # Prefer confirm-approval over get_prompts_status (sustained 429s).
            with patch_device_approval(
                timeout_seconds=timeout,
                poll_seconds=poll_seconds,
                max_empty_polls=max_empty,
                skip_status_poll=skip_status,
            ):
                result = rh.login(
                    username=self.settings.robinhood_username,
                    password=self.settings.robinhood_password,
                    mfa_code=mfa_code,
                    expiresIn=86400,
                )
        except Exception as exc:
            return False, classify_login_error(exc)

        # robin_stocks.login swallows verification errors and returns None.
        swallowed = take_last_error()
        if result:
            return True, None
        if swallowed is not None:
            return False, classify_login_error(
                swallowed if isinstance(swallowed, Exception) else Exception(str(swallowed))
            )
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
    if isinstance(exc, RobinhoodRateLimitedError):
        return "robinhood_rate_limited"
    if isinstance(exc, RobinhoodVerificationTimeout):
        return "robinhood_verification_required"
    message = str(exc).lower()
    if "429" in message or "too many requests" in message:
        return "robinhood_rate_limited"
    if "nonetype" in message and "subscriptable" in message:
        # Upstream crash when get_prompts_status returns None after a 429.
        return "robinhood_rate_limited"
    if "verification" in message or "challenge" in message or "device approval" in message:
        return "robinhood_verification_required"
    if "timeout" in message:
        return "robinhood_verification_required"
    return "robinhood_login_failed"
