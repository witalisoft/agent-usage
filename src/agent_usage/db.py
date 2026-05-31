"""SQLite persistence layer — sessions and samples."""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import attrs

from agent_usage.paths import db_path as default_db_path


def _adapt_datetime(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _convert_datetime(val: bytes) -> datetime:
    return datetime.fromisoformat(val.decode()).replace(tzinfo=UTC)


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)

_SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id          INTEGER PRIMARY KEY,
        agent       TEXT    NOT NULL,
        session_id  TEXT,
        cwd         TEXT    NOT NULL,
        started_at  TEXT    NOT NULL,
        ended_at    TEXT,
        end_reason  TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_agent_started ON sessions(agent, started_at)",
]

_SCHEMA_VERSION = 1


@attrs.define
class Database:
    path: Path = attrs.Factory(default_db_path)
    _conn: sqlite3.Connection = attrs.field(init=False)

    def __attrs_post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path),
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
            timeout=5.0,  # busy-wait up to 5s for WAL mode switch and schema writes
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        # WAL mode change needs an exclusive lock; retry if another process is doing the same.
        for attempt in range(50):
            try:
                self._conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError:
                if attempt == 49:
                    raise
                time.sleep(0.01)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._create_tables()
        self._migrate()

    def _create_tables(self) -> None:
        for stmt in _SCHEMA_STATEMENTS:
            self._conn.execute(stmt)
        self._conn.commit()

    def _migrate(self) -> None:
        row = self._conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_version VALUES (?)", (_SCHEMA_VERSION,))
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── write ──────────────────────────────────────────────────────────

    def start_session(self, agent: str, session_id: str | None, cwd: str) -> int:
        ts = datetime.now(tz=UTC).isoformat()
        cur = self._conn.execute(
            "INSERT INTO sessions (agent, session_id, cwd, started_at) VALUES (?, ?, ?, ?)",
            (agent, session_id, cwd, ts),
        )
        self._conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def end_session(self, session_row_id: int, reason: str) -> None:
        ts = datetime.now(tz=UTC).isoformat()
        self._conn.execute(
            "UPDATE sessions SET ended_at=?, end_reason=? WHERE id=?",
            (ts, reason, session_row_id),
        )
        self._conn.commit()

    def update_session_agent_id(self, session_row_id: int, session_id: str) -> None:
        self._conn.execute(
            "UPDATE sessions SET session_id=? WHERE id=?",
            (session_id, session_row_id),
        )
        self._conn.commit()

    # ── read ───────────────────────────────────────────────────────────

    def sessions_last_n_days(self, agent: str, days: int) -> int:
        """Count sessions started in the last N days."""
        cutoff = _days_ago_iso(days)
        row = self._conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE agent=? AND started_at >= ?",
            (agent, cutoff),
        ).fetchone()
        return row[0]

    def last_used(self, agent: str) -> str | None:
        """ISO-8601 UTC of the most recent session end (or start if still running), or None."""
        row = self._conn.execute(
            "SELECT COALESCE(ended_at, started_at) FROM sessions WHERE agent=? "
            "ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1",
            (agent,),
        ).fetchone()
        return row[0] if row else None

    def raw_execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """For tests only."""
        return self._conn.execute(sql, params).fetchall()


def _days_ago_iso(days: int) -> str:
    from datetime import timedelta

    return (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
