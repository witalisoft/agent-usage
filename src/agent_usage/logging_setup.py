"""Per-instance plain-text file logging."""

import contextlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from agent_usage.paths import log_dir, log_path

_MAX_LOG_FILES = 20


def _prune_old_logs(directory: Path, keep: int) -> None:
    """Delete oldest agent-usage-*.log files beyond `keep` newest."""
    logs = sorted(directory.glob("agent-usage-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[keep:]:
        with contextlib.suppress(OSError):
            old.unlink()


def setup_logger(agent: str) -> tuple[logging.Logger, Path]:
    """Create per-instance logger; return (logger, log_file_path).

    Prunes old log files on startup.
    """
    pid = os.getpid()
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")

    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)

    _prune_old_logs(directory, _MAX_LOG_FILES)

    file_path = log_path(agent, pid, ts)

    logger = logging.getLogger(f"agent_usage.{agent}.{pid}")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        handler = logging.FileHandler(file_path, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.propagate = False

    return logger, file_path
