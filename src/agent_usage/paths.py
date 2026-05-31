"""Platform-resolved paths via platformdirs."""

from pathlib import Path

import platformdirs

APP_NAME = "agent-usage"


def config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME))


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME))


def log_dir() -> Path:
    return Path(platformdirs.user_log_dir(APP_NAME))


def db_path() -> Path:
    return data_dir() / "usage.db"


def config_path() -> Path:
    return config_dir() / "config.yaml"


def log_path(agent: str, pid: int, ts: str) -> Path:
    """Per-instance log file: agent-usage-<agent>-<pid>-<ts>.log"""
    return log_dir() / f"agent-usage-{agent}-{pid}-{ts}.log"
