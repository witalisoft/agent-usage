"""Tests for menu helpers."""

from datetime import UTC, datetime, timedelta

from agent_usage.menu import _format_age, _format_last_used, _metrics_str, _short_id


def test_format_last_used_just_now():
    last_used = (datetime.now(tz=UTC) - timedelta(seconds=20)).isoformat()
    assert _format_last_used(last_used) == "just now"


def test_format_last_used_minutes():
    last_used = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
    assert _format_last_used(last_used) == "5m ago"


def test_format_age_hours():
    dt = datetime.now(tz=UTC) - timedelta(hours=3)
    assert _format_age(dt) == "3h ago"


def test_format_age_days():
    dt = datetime.now(tz=UTC) - timedelta(days=2)
    assert _format_age(dt) == "2d ago"


def test_format_age_none():
    assert _format_age(None) == "never"


def test_metrics_str_with_usage():
    result = _metrics_str({"5h": 50.0, "7day": 25.0})
    assert "5h" in result
    assert "50%" in result
    assert "7day" in result


def test_metrics_str_empty():
    assert _metrics_str({}) == "(usage unavailable)"


def test_short_id_truncates():
    long_id = "abcdef1234567890"
    assert _short_id(long_id) == "abcdef12…"


def test_short_id_short():
    short = "abc"
    assert _short_id(short) == "abc"
