"""Tests for agent provider parse logic and detection."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_usage.agents.base import UsageFetchError
from agent_usage.agents.claude import ClaudeProvider
from agent_usage.agents.claude import _parse_usage as claude_parse
from agent_usage.agents.codex import CodexProvider
from agent_usage.agents.codex import _parse_usage as codex_parse
from agent_usage.agents.copilot import CopilotProvider


def test_claude_parse_usage():
    data = {
        "five_hour": {"utilization": 62.5},
        "seven_day": {"utilization": 40.0},
    }
    result = claude_parse(data)
    assert result["5h"] == 62.5
    assert result["7day"] == 40.0


def test_claude_parse_usage_missing_fields():
    with pytest.raises(UsageFetchError):
        claude_parse({})


def test_codex_parse_usage():
    data = {
        "rate_limit": {
            "primary_window": {"used_percent": 55.0, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 20.0, "limit_window_seconds": 604800},
        }
    }
    result = codex_parse(data)
    assert result["5h"] == 55.0
    assert result["weekly"] == 20.0


def test_codex_parse_empty():
    with pytest.raises(UsageFetchError):
        codex_parse({})


def test_copilot_percent_calculation():
    provider = CopilotProvider(plan="pro")
    with (
        patch("agent_usage.agents.copilot._get_token", return_value="tok"),
        patch("agent_usage.agents.copilot._fetch_username", return_value="alice"),
        patch("requests.get") as mock_get,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "usageItems": [
                {"grossQuantity": 100},
                {"grossQuantity": 50},
            ]
        }
        result = provider.get_usage()
    assert result["monthly"] == pytest.approx(50.0)  # 150/300*100


def test_copilot_parse_empty():
    provider = CopilotProvider(plan="pro")
    with (
        patch("agent_usage.agents.copilot._get_token", return_value="tok"),
        patch("agent_usage.agents.copilot._fetch_username", return_value="alice"),
        patch("agent_usage.agents.copilot.requests.get") as mock_get,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"usageItems": []}
        with pytest.raises(UsageFetchError):
            provider.get_usage()


def test_copilot_json_decode_error():
    provider = CopilotProvider(plan="pro")
    with (
        patch("agent_usage.agents.copilot._get_token", return_value="tok"),
        patch("agent_usage.agents.copilot._fetch_username", return_value="alice"),
        patch("agent_usage.agents.copilot.requests.get") as mock_get,
    ):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "", 0)
        with pytest.raises(UsageFetchError):
            provider.get_usage()


def test_claude_detection():
    provider = ClaudeProvider()
    with patch("shutil.which", return_value="/usr/bin/claude"):
        assert provider.detect() is True
    with patch("shutil.which", return_value=None):
        assert provider.detect() is False


def test_copilot_session_id_no_db(tmp_path):
    provider = CopilotProvider(plan="pro")
    with patch("agent_usage.agents.copilot._session_db_path", return_value=tmp_path / "nonexistent.db"):
        result = provider.current_session_id("/some/cwd")
    assert result is None


def test_claude_session_id():
    from agent_usage.agents.claude import _cwd_slug

    assert _cwd_slug("/Users/alice/my-proj") == "-Users-alice-my-proj"


def test_claude_list_sessions(tmp_path):
    """list_sessions returns SessionInfo for each JSONL file, newest first."""
    import time

    slug = tmp_path / "projects" / "-my-proj"
    slug.mkdir(parents=True)
    old_file = slug / "aaaa1111.jsonl"
    old_file.write_text("{}")
    time.sleep(0.01)
    new_file = slug / "bbbb2222.jsonl"
    new_file.write_text("{}")

    with patch("agent_usage.agents.claude.Path") as mock_path:
        # Patch home() to return tmp_path so the provider finds our test dir.
        mock_path.home.return_value = tmp_path
        mock_path.side_effect = lambda *a: tmp_path.joinpath(*a) if a else tmp_path
        # Call directly with the constructed path to avoid Path() mock complexity.
        from agent_usage.agents.claude import _cwd_slug

        project_dir = tmp_path / "projects" / _cwd_slug("/my-proj")
        sessions = [
            __import__("agent_usage.agents.base", fromlist=["SessionInfo"]).SessionInfo(
                session_id=f.stem,
                updated_at=__import__("datetime").datetime.fromtimestamp(
                    f.stat().st_mtime, tz=__import__("datetime").timezone.utc
                ),
            )
            for f in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        ]
    assert sessions[0].session_id == "bbbb2222"
    assert sessions[1].session_id == "aaaa1111"


def test_copilot_list_sessions_no_db(tmp_path):
    """list_sessions returns empty list when session DB is absent."""
    provider = CopilotProvider(plan="pro")
    with patch("agent_usage.agents.copilot._session_db_path", return_value=tmp_path / "nonexistent.db"):
        result = provider.list_sessions("/some/cwd")
    assert result == []


def test_codex_list_sessions_no_db():
    """list_sessions returns empty list when no state DB is found."""
    provider = CodexProvider()
    with patch("agent_usage.agents.codex._state_db_path", return_value=None):
        result = provider.list_sessions("/some/cwd")
    assert result == []


def test_codex_list_sessions_summary(tmp_path):
    """list_sessions populates summary from title, falls back to first_user_message."""
    import sqlite3
    import time as _time

    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE threads (id TEXT, cwd TEXT, updated_at INTEGER, title TEXT, first_user_message TEXT)")
    now = int(_time.time())
    conn.execute(
        "INSERT INTO threads VALUES (?,?,?,?,?)",
        ("id-with-title", "/proj", now, "My title", "first msg"),
    )
    conn.execute(
        "INSERT INTO threads VALUES (?,?,?,?,?)",
        ("id-no-title", "/proj", now - 1, "", "fallback msg"),
    )
    conn.execute(
        "INSERT INTO threads VALUES (?,?,?,?,?)",
        ("id-no-summary", "/proj", now - 2, "", ""),
    )
    conn.commit()
    conn.close()

    provider = CodexProvider()
    with patch("agent_usage.agents.codex._state_db_path", return_value=db):
        result = provider.list_sessions("/proj")

    assert result[0].session_id == "id-with-title"
    assert result[0].summary == "My title"
    assert result[1].session_id == "id-no-title"
    assert result[1].summary == "fallback msg"
    assert result[2].session_id == "id-no-summary"
    assert result[2].summary is None


def test_copilot_list_sessions_summary(tmp_path):
    """list_sessions populates summary from the sessions table summary column."""
    import sqlite3

    db = tmp_path / "session-store.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, updated_at TEXT, summary TEXT)")
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?)",
        ("sess-a", "/proj", "2025-01-01T00:00:00", "Did some refactoring"),
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?,?,?,?)",
        ("sess-b", "/proj", "2024-12-31T00:00:00", None),
    )
    conn.commit()
    conn.close()

    provider = CopilotProvider(plan="pro")
    with patch("agent_usage.agents.copilot._session_db_path", return_value=db):
        result = provider.list_sessions("/proj")

    assert result[0].session_id == "sess-a"
    assert result[0].summary == "Did some refactoring"
    assert result[1].session_id == "sess-b"
    assert result[1].summary is None


def test_copilot_with_wrong_plan_selected():
    with pytest.raises(ValueError, match=r"Unknown Copilot plan"):
        CopilotProvider(plan="unknown")


def test_claude_read_first_user_message(tmp_path):
    """_read_first_user_message skips system lines and returns the first real user message."""
    import json

    from agent_usage.agents.claude import _read_first_user_message

    jsonl = tmp_path / "session.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": "Hi"}},
        {"type": "user", "message": {"content": "<local-command-caveat>ignore this"}},
        {"type": "user", "message": {"content": "Hello, can you help me with a bug?"}},
        {"type": "user", "message": {"content": "Second real message"}},
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines))

    result = _read_first_user_message(jsonl)
    assert result == "Hello, can you help me with a bug?"


def test_claude_read_first_user_message_list_content(tmp_path):
    """_read_first_user_message handles content as a list of blocks."""
    import json

    from agent_usage.agents.claude import _read_first_user_message

    jsonl = tmp_path / "session.jsonl"
    lines = [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "<command-name>skip"},
                    {"type": "text", "text": "Real request from user"},
                ]
            },
        },
    ]
    jsonl.write_text("\n".join(json.dumps(line) for line in lines))

    result = _read_first_user_message(jsonl)
    assert result == "Real request from user"


def test_claude_read_first_user_message_no_messages(tmp_path):
    """_read_first_user_message returns None when no real user messages exist."""
    import json

    from agent_usage.agents.claude import _read_first_user_message

    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(json.dumps({"type": "assistant", "message": {"content": "Hi"}}))

    result = _read_first_user_message(jsonl)
    assert result is None


# ---------------------------------------------------------------------------
# _save_keychain_credentials
# ---------------------------------------------------------------------------


def test_save_keychain_credentials_darwin(monkeypatch):
    """_save_keychain_credentials writes back to Keychain on macOS."""
    import json as _json
    import platform as _platform

    from agent_usage.agents.claude import _save_keychain_credentials

    monkeypatch.setattr(_platform, "system", lambda: "Darwin")

    existing = {"claudeAiOauth": {"accessToken": "old", "refreshToken": "rt"}}
    new_creds = {"accessToken": "new", "refreshToken": "rt2", "expiresAt": "9999"}

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        mock = MagicMock()
        mock.returncode = 0
        if "find-generic-password" in cmd and "-w" not in cmd:
            mock.stdout = '"acct"<blob>="user@example.com"\n'
        elif "find-generic-password" in cmd and "-w" in cmd:
            mock.stdout = _json.dumps(existing) + "\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    with patch("agent_usage.agents.claude.subprocess.run", side_effect=fake_run):
        _save_keychain_credentials(new_creds)

    # The last call must be add-generic-password -U with the new JSON.
    add_cmd = calls[-1]
    assert "add-generic-password" in add_cmd
    assert "-U" in add_cmd
    assert "user@example.com" in add_cmd
    written = _json.loads(add_cmd[add_cmd.index("-w") + 1])
    assert written["claudeAiOauth"]["accessToken"] == "new"
    assert written["claudeAiOauth"]["refreshToken"] == "rt2"


def test_save_keychain_credentials_non_darwin(monkeypatch):
    """_save_keychain_credentials is a no-op on non-macOS platforms."""
    import platform as _platform

    from agent_usage.agents.claude import _save_keychain_credentials

    monkeypatch.setattr(_platform, "system", lambda: "Linux")

    with patch("agent_usage.agents.claude.subprocess.run") as mock_run:
        _save_keychain_credentials({"accessToken": "tok"})
        mock_run.assert_not_called()


def test_ensure_fresh_credentials_keychain_saves_back(monkeypatch):
    """Refreshed tokens from Keychain are written back to Keychain, not only
    to the credentials file."""
    import time as _time

    from agent_usage.agents.claude import _ensure_fresh_credentials

    expired_creds = {
        "accessToken": "old",
        "refreshToken": "rt",
        "expiresAt": 0,  # always expired
    }
    refreshed_creds = {
        "accessToken": "new",
        "refreshToken": "rt2",
        "expiresAt": int((_time.time() + 3600) * 1000),
    }

    with (
        patch(
            "agent_usage.agents.claude._load_credentials",
            return_value=None,
        ),
        patch(
            "agent_usage.agents.claude._load_keychain_credentials",
            return_value=expired_creds,
        ),
        patch(
            "agent_usage.agents.claude._refresh_access_token",
            return_value=refreshed_creds,
        ),
        patch("agent_usage.agents.claude._save_credentials") as mock_save_file,
        patch("agent_usage.agents.claude._save_keychain_credentials") as mock_save_kc,
    ):
        result = _ensure_fresh_credentials()

    assert result["accessToken"] == "new"
    mock_save_file.assert_called_once_with(refreshed_creds)
    # Must also have saved back to the Keychain.
    mock_save_kc.assert_called_once_with(refreshed_creds)


def test_ensure_fresh_credentials_file_no_keychain_save(monkeypatch):
    """When tokens come only from the file, Keychain is NOT written."""
    import time as _time

    from agent_usage.agents.claude import _ensure_fresh_credentials

    expired_creds = {
        "accessToken": "old",
        "refreshToken": "rt",
        "expiresAt": 0,
    }
    refreshed_creds = {
        "accessToken": "new",
        "refreshToken": "rt2",
        "expiresAt": int((_time.time() + 3600) * 1000),
    }

    with (
        patch(
            "agent_usage.agents.claude._load_credentials",
            return_value=expired_creds,
        ),
        patch(
            "agent_usage.agents.claude._load_keychain_credentials",
            return_value=None,
        ),
        patch(
            "agent_usage.agents.claude._refresh_access_token",
            return_value=refreshed_creds,
        ),
        patch("agent_usage.agents.claude._save_credentials") as mock_save_file,
        patch("agent_usage.agents.claude._save_keychain_credentials") as mock_save_kc,
    ):
        result = _ensure_fresh_credentials()

    assert result["accessToken"] == "new"
    mock_save_file.assert_called_once_with(refreshed_creds)
    # File-only path must NOT touch the Keychain.
    mock_save_kc.assert_not_called()


def test_ensure_fresh_credentials_keychain_fallback_saves_back():
    """Fallback keychain path (file refresh failed) also saves back to Keychain."""
    import time as _time

    from agent_usage.agents.claude import _ensure_fresh_credentials

    expired_file = {"accessToken": "old", "refreshToken": "bad-rt", "expiresAt": 0}
    expired_kc = {"accessToken": "kc-old", "refreshToken": "kc-rt", "expiresAt": 0}
    refreshed = {
        "accessToken": "kc-new",
        "refreshToken": "kc-rt2",
        "expiresAt": int((_time.time() + 3600) * 1000),
    }

    kc_call_count = 0

    def load_kc():
        nonlocal kc_call_count
        kc_call_count += 1
        return expired_kc

    def refresh(creds):
        if creds.get("refreshToken") == "bad-rt":
            raise UsageFetchError("bad token")
        return refreshed

    with (
        patch("agent_usage.agents.claude._load_credentials", return_value=expired_file),
        patch("agent_usage.agents.claude._load_keychain_credentials", side_effect=load_kc),
        patch("agent_usage.agents.claude._refresh_access_token", side_effect=refresh),
        patch("agent_usage.agents.claude._save_credentials") as mock_save_file,
        patch("agent_usage.agents.claude._save_keychain_credentials") as mock_save_kc,
    ):
        result = _ensure_fresh_credentials()

    assert result["accessToken"] == "kc-new"
    mock_save_file.assert_called_with(refreshed)
    mock_save_kc.assert_called_with(refreshed)
