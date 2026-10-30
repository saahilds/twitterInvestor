from __future__ import annotations

import os
import subprocess
import time

from app.config.settings import Settings
from app.ingestion.backfill_lock import backfill_in_progress, backfill_lock_path


def _settings(tmp_path) -> Settings:
    return Settings(
        log_file=str(tmp_path / "bot.log"),
        broker_backend="mock",
        snapshot_enabled=False,
        daily_digest_enabled=False,
    )


def test_live_lock_holder_blocks_chrome_quit_after_the_mtime_window(tmp_path) -> None:
    settings = _settings(tmp_path)
    path = backfill_lock_path(settings)
    path.write_text(str(os.getpid()), encoding="utf-8")
    old = time.time() - (45 * 60)
    os.utime(path, (old, old))

    assert backfill_in_progress(settings) is True


def test_dead_lock_holder_does_not_keep_chrome_up(tmp_path) -> None:
    finished = subprocess.Popen(["python3", "-c", "pass"])
    finished.wait(timeout=10)
    settings = _settings(tmp_path)
    path = backfill_lock_path(settings)
    path.write_text(str(finished.pid), encoding="utf-8")

    assert backfill_in_progress(settings) is False


def test_fresh_lock_without_a_pid_still_counts(tmp_path) -> None:
    settings = _settings(tmp_path)
    path = backfill_lock_path(settings)
    path.write_text("backfill", encoding="utf-8")

    assert backfill_in_progress(settings) is True
