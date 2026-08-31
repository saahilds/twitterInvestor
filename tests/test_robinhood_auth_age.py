from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.execution import robinhood_auth_age as auth_age


@pytest.fixture()
def token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tokens = tmp_path / ".tokens"
    tokens.mkdir()
    monkeypatch.setattr(auth_age, "tokens_dir", lambda: tokens)
    return tokens


def test_compute_auth_age_ok_warn_critical(token_dir: Path) -> None:
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    (token_dir / "robinhood.pickle").write_bytes(b"x")

    auth_age.record_successful_auth(moment=now - timedelta(days=2))
    snap = auth_age.compute_auth_age(max_age_days=6, warn_days=1, now=now)
    assert snap.status == "ok"
    assert snap.days_remaining == pytest.approx(4.0, abs=0.01)

    auth_age.record_successful_auth(moment=now - timedelta(days=5.5))
    snap = auth_age.compute_auth_age(max_age_days=6, warn_days=1, now=now)
    assert snap.status == "warn"
    assert snap.days_remaining == pytest.approx(0.5, abs=0.01)

    auth_age.record_successful_auth(moment=now - timedelta(days=7))
    snap = auth_age.compute_auth_age(max_age_days=6, warn_days=1, now=now)
    assert snap.status == "critical"
    assert snap.days_remaining is not None and snap.days_remaining <= 0


def test_compute_auth_age_missing(token_dir: Path) -> None:
    snap = auth_age.compute_auth_age(max_age_days=6, warn_days=1)
    assert snap.status == "missing"
    assert snap.pickle_exists is False


def test_meta_write_read_and_alert_dedupe(token_dir: Path) -> None:
    stamped = auth_age.record_successful_auth(
        moment=datetime(2026, 7, 20, tzinfo=timezone.utc)
    )
    meta = auth_age.read_auth_meta()
    assert meta["last_authenticated_at"].startswith("2026-07-20")
    assert stamped.year == 2026

    assert auth_age.should_alert_auth_status("ok") is False
    assert auth_age.should_alert_auth_status("warn") is True
    auth_age.mark_auth_alert_sent("warn")
    assert auth_age.should_alert_auth_status("warn") is False
    assert auth_age.should_alert_auth_status("critical") is True


def test_seed_from_pickle_mtime(token_dir: Path) -> None:
    pickle = token_dir / "robinhood.pickle"
    pickle.write_bytes(b"session")
    past = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc).timestamp()
    import os

    os.utime(pickle, (past, past))
    seeded = auth_age.seed_auth_meta_from_pickle_mtime()
    assert seeded is not None
    assert seeded.day == 10
    again = auth_age.resolve_last_authenticated_at()
    assert again == seeded
