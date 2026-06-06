from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from app.config.settings import Settings

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


@dataclass
class _SessionState:
    logged_in: bool = False
    last_error: str | None = None
    blocked_until: float = 0.0
    last_success_at: float | None = None
    last_validated_at: float | None = None
    consecutive_failures: int = 0
    cooldown_logged_at: float = 0.0


class RobinhoodSessionManager:
    """Single-flight Robinhood login with cooldowns after failures / 429s."""

    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._lock = threading.RLock()
        self._state = _SessionState()

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return SessionSnapshot(
                logged_in=self._state.logged_in,
                last_error=self._state.last_error,
                blocked_until_monotonic=self._state.blocked_until,
                consecutive_failures=self._state.consecutive_failures,
                last_success_monotonic=self._state.last_success_at,
            )

    def invalidate(self, *, reason: str | None = None) -> None:
        with self._lock:
            self._state.logged_in = False
            self._state.last_validated_at = None
            if reason:
                self._state.last_error = reason

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

            if self._state.logged_in:
                if self._session_recently_validated(now) or self._validate_existing_session(now):
                    return None

            ok, error = self._perform_login()
            finished = time.monotonic()
            if ok:
                self._state.logged_in = True
                self._state.last_error = None
                self._state.consecutive_failures = 0
                self._state.last_success_at = finished
                self._state.last_validated_at = finished
                self._state.blocked_until = 0.0
                self.logger.info(
                    "robinhood_login_success",
                    extra={"event_type": "robinhood_auth"},
                )
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
            return self._state.last_error

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
