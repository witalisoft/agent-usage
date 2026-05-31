"""Tests for SQLite layer."""

import threading
from datetime import UTC, datetime, timedelta

import pytest

from agent_usage.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(path=tmp_path / "test.db")
    yield d
    d.close()


def test_start_end_session(db):
    row_id = db.start_session("claude", "sess-1", "/tmp/proj")
    assert row_id > 0
    db.end_session(row_id, "exit")
    rows = db.raw_execute("SELECT * FROM sessions WHERE id=?", (row_id,))
    assert len(rows) == 1
    assert rows[0]["end_reason"] == "exit"
    assert rows[0]["ended_at"] is not None


def test_sessions_last_n_days(db):
    # Past session (31 days ago)
    old_ts = (datetime.now(tz=UTC) - timedelta(days=31)).isoformat()
    db._conn.execute(
        "INSERT INTO sessions (agent, cwd, started_at) VALUES (?, ?, ?)",
        ("claude", "/tmp", old_ts),
    )
    # Recent session
    db.start_session("claude", None, "/tmp")
    db._conn.commit()
    count = db.sessions_last_n_days("claude", 30)
    assert count == 1


def test_concurrent_writers(tmp_path):
    """Two threads each write sessions; no corruption or lock errors expected."""
    db_path = tmp_path / "concurrent.db"
    errors: list[Exception] = []

    def writer(agent: str) -> None:
        db = Database(path=db_path)
        try:
            row_id = db.start_session(agent, None, "/tmp")
            db.end_session(row_id, "exit")
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    t1 = threading.Thread(target=writer, args=("claude",))
    t2 = threading.Thread(target=writer, args=("codex",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == [], f"Concurrent write errors: {errors}"

    verify = Database(path=db_path)
    count = verify.raw_execute("SELECT COUNT(*) FROM sessions")[0][0]
    assert count == 2
    verify.close()


def test_last_used_prefers_ended_at(db):
    older = db.start_session("claude", "sess-1", "/tmp/proj")
    db._conn.execute(
        "UPDATE sessions SET started_at=?, ended_at=? WHERE id=?",
        (
            "2026-05-01T10:00:00+00:00",
            "2026-05-01T11:00:00+00:00",
            older,
        ),
    )

    newer_started_earlier = db.start_session("claude", "sess-2", "/tmp/proj")
    db._conn.execute(
        "UPDATE sessions SET started_at=?, ended_at=? WHERE id=?",
        (
            "2026-05-01T10:30:00+00:00",
            "2026-05-01T10:45:00+00:00",
            newer_started_earlier,
        ),
    )
    db._conn.commit()

    assert db.last_used("claude") == "2026-05-01T11:00:00+00:00"


def test_last_used_falls_back_to_started_at_for_running_session(db):
    row_id = db.start_session("codex", "sess-3", "/tmp/proj")
    db._conn.execute(
        "UPDATE sessions SET started_at=? WHERE id=?",
        ("2026-05-02T09:15:00+00:00", row_id),
    )
    db._conn.commit()

    assert db.last_used("codex") == "2026-05-02T09:15:00+00:00"
