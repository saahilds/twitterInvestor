from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config.settings import Settings

_LOCK_MAX_AGE_SECONDS = 30 * 60
_LOCK_REFRESH_SECONDS = 60


def backfill_lock_path(settings: Settings) -> Path:
    return Path(settings.log_file).expanduser().resolve().parent / "backfill.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def backfill_in_progress(settings: Settings) -> bool:
    """True while a backfill process holds the lock, so the worker leaves Chrome alone."""
    path = backfill_lock_path(settings)
    try:
        if not path.is_file():
            return False
        age = time.time() - path.stat().st_mtime
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if text.isdigit():
        return _pid_alive(int(text))
    return age < _LOCK_MAX_AGE_SECONDS


@contextmanager
def hold_backfill_lock(settings: Settings) -> Iterator[None]:
    path = backfill_lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")
    stop = threading.Event()

    def refresh() -> None:
        while not stop.wait(_LOCK_REFRESH_SECONDS):
            try:
                os.utime(path, None)
            except OSError:
                return

    refresher = threading.Thread(target=refresh, name="backfill-lock", daemon=True)
    refresher.start()
    try:
        yield
    finally:
        stop.set()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
