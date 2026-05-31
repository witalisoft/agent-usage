"""Tests for poller crossing detection and script firing."""

from unittest.mock import MagicMock, patch

from agent_usage.config import AgentConfig, ScriptRule
from agent_usage.poller import Poller


def _make_poller(tmp_path, usage_seq, scripts=None):
    from agent_usage.db import Database

    db = Database(path=tmp_path / "test.db")
    row_id = db.start_session("claude", None, "/tmp")

    agent = MagicMock()
    agent.name = "claude"
    agent.get_usage.side_effect = usage_seq

    acfg = AgentConfig(
        scripts=scripts or [],
    )

    logger = MagicMock()
    poller = Poller(
        agent=agent,
        agent_cfg=acfg,
        db=db,
        session_row_id=row_id,
        poll_interval=60,
        logger=logger,
    )
    return poller, db


def _mock_run(returncode: int = 0):
    """Return a mock for subprocess.run that captures env and simulates exit code."""
    result = MagicMock()
    result.returncode = returncode
    result.stderr = "script failed" if returncode != 0 else ""
    result.stdout = ""
    return result


def test_crossing_fires_script_once(tmp_path):
    fired: list[dict] = []

    def fake_run(*a, **kw):
        fired.append(kw.get("env", {}))
        return _mock_run()

    with patch("agent_usage.poller.subprocess.run", side_effect=fake_run):
        usage_seq = [
            {"5h": 50.0},  # under
            {"5h": 85.0},  # over → fire
            {"5h": 90.0},  # over → no re-fire (not re-armed)
        ]
        scripts = [ScriptRule(metric="5h", threshold=80.0, cmd="echo hit")]
        poller, db = _make_poller(tmp_path, usage_seq, scripts=scripts)

        for _ in range(3):
            poller._tick()

    # Give the daemon thread a moment to run
    import time

    time.sleep(0.05)

    assert len(fired) == 1
    assert fired[0]["AGENT"] == "claude"
    assert fired[0]["LIMIT_NAME"] == "5h"
    db.close()


def test_script_re_armed_after_drop(tmp_path):
    fired: list[dict] = []

    def fake_run(*a, **kw):
        fired.append(kw.get("env", {}))
        return _mock_run()

    with patch("agent_usage.poller.subprocess.run", side_effect=fake_run):
        usage_seq = [
            {"5h": 50.0},  # under
            {"5h": 85.0},  # over → fire #1
            {"5h": 60.0},  # drop under → re-arm
            {"5h": 85.0},  # over → fire #2
        ]
        scripts = [ScriptRule(metric="5h", threshold=80.0, cmd="echo hit")]
        poller, db = _make_poller(tmp_path, usage_seq, scripts=scripts)

        for _ in range(4):
            poller._tick()

    import time

    time.sleep(0.05)
    assert len(fired) == 2
    db.close()


def test_threshold_exact_value(tmp_path):
    fired: list = []

    def fake_run(*a, **kw):
        fired.append(True)
        return _mock_run()

    with patch("agent_usage.poller.subprocess.run", side_effect=fake_run):
        usage_seq = [{"5h": 79.99}, {"5h": 80.0}]
        scripts = [ScriptRule(metric="5h", threshold=80.0, cmd="echo hit")]
        poller, db = _make_poller(tmp_path, usage_seq, scripts=scripts)

        for _ in range(2):
            poller._tick()

    import time

    time.sleep(0.05)
    assert len(fired) == 1
    db.close()


def test_script_timeout(tmp_path):
    import subprocess

    def fake_run_timeout(*a, **kw):
        raise subprocess.TimeoutExpired("echo hit", 30)

    with patch("agent_usage.poller.subprocess.run", side_effect=fake_run_timeout):
        usage_seq = [
            {"5h": 50.0},  # under
            {"5h": 85.0},  # over → fire and timeout
        ]
        scripts = [ScriptRule(metric="5h", threshold=80.0, cmd="echo hit")]
        poller, db = _make_poller(tmp_path, usage_seq, scripts=scripts)

        for _ in range(2):
            poller._tick()

    import time

    time.sleep(0.05)
    # Verify timeout was logged
    assert poller.logger.warning.called
    # Check that the warning was called with timeout message
    calls = poller.logger.warning.call_args_list
    timeout_logged = any("Script timeout" in str(call) for call in calls)
    assert timeout_logged
    db.close()
