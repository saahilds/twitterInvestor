from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.clients import _extract_status_id, _parse_x_datetime


def test_extract_status_id() -> None:
    assert _extract_status_id("/CKCapitalxx/status/19246123123456789") == "19246123123456789"
    assert _extract_status_id("https://x.com/CKCapitalxx/status/42") == "42"
    assert _extract_status_id("/CKCapitalxx") is None


def test_parse_x_datetime_uses_iso_value() -> None:
    parsed = _parse_x_datetime("2026-05-20T14:30:00.000Z")
    assert parsed.year == 2026
    assert parsed.month == 5
    assert parsed.day == 20
    assert parsed.tzinfo is not None


def test_parse_x_datetime_falls_back_to_now_for_invalid_value() -> None:
    before = datetime.now(timezone.utc)
    parsed = _parse_x_datetime("not-a-real-date")
    after = datetime.now(timezone.utc)
    assert before <= parsed <= after
