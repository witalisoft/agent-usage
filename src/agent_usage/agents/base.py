"""Agent interface and domain exceptions."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from datetime import datetime

import attrs


class UsageFetchError(RuntimeError):
    pass


@attrs.define
class SessionInfo:
    """Metadata for a single agent session scoped to a working directory."""

    session_id: str
    updated_at: datetime | None = None
    summary: str | None = None  # first user message or agent-provided title


class Agent(ABC):
    name: str
    primary_metric: str

    @abstractmethod
    def detect(self) -> bool:
        """Return True if the agent binary/tooling is installed."""

    @abstractmethod
    def get_usage(self) -> dict[str, float]:
        """Return metric name → percent (0-100). Raises UsageFetchError on failure."""

    @abstractmethod
    def current_session_id(self, cwd: str) -> str | None:
        """Return the cwd-scoped current session id, or None."""

    @abstractmethod
    def list_sessions(self, cwd: str) -> list[SessionInfo]:
        """Return all cwd-scoped sessions for this agent, newest first."""

    @abstractmethod
    def spawn_interactive(self, cwd: str) -> subprocess.Popen:
        """Start agent interactively; return the process (caller must .wait())."""

    @abstractmethod
    def resume_interactive(self, session_id: str, cwd: str) -> subprocess.Popen:
        """Resume a specific session interactively; return the process (caller must .wait())."""
