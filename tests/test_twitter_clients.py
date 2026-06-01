from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.clients import _as_utc, _extract_status_id, _parse_x_datetime, _rows_to_tweets


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


def test_rows_to_tweets_deduplicates_ids() -> None:
    rows = [
        {
            "href": "/CKCapitalxx/status/111",
            "datetime": "2026-05-20T14:30:00.000Z",
            "text": "adding NVDA",
            "isReply": False,
            "isRetweet": False,
        },
        {
            "href": "/CKCapitalxx/status/111",
            "datetime": "2026-05-20T14:30:00.000Z",
            "text": "duplicate row",
            "isReply": False,
            "isRetweet": False,
        },
    ]
    tweets = _rows_to_tweets(rows)
    assert len(tweets) == 1
    assert tweets[0].tweet_id == "111"


def test_as_utc_adds_timezone_to_naive_datetime() -> None:
    naive = datetime(2026, 1, 1, 9, 30, 0)
    converted = _as_utc(naive)
    assert converted.tzinfo is not None
