"""Session lifecycle — launch agent, manage poller, write DB records."""

from __future__ import annotations

import logging
import subprocess
import time

import attrs

from agent_usage.agents.base import Agent
from agent_usage.config import AgentConfig
from agent_usage.db import Database
from agent_usage.poller import Poller


@attrs.define
class Runner:
    agent: Agent
    agent_cfg: AgentConfig
    db: Database
    logger: logging.Logger
    poll_interval: int
    cwd: str
    session_id: str | None = None  # if set, resume this existing session instead of spawning fresh
    _session_row_id: int = attrs.field(init=False, default=-1)
    _poller: Poller | None = attrs.field(init=False, default=None)
    _proc: subprocess.Popen | None = attrs.field(init=False, default=None)

    def run(self) -> str:
        """Launch or resume the agent interactively; return end_reason."""
        self._session_row_id = self.db.start_session(self.agent.name, self.session_id, self.cwd)

        self._poller = Poller(
            agent=self.agent,
            agent_cfg=self.agent_cfg,
            db=self.db,
            session_row_id=self._session_row_id,
            poll_interval=self.poll_interval,
            logger=self.logger,
        )
        self._poller.start()

        end_reason = "exit"
        try:
            if self.session_id:
                # Resume a known session directly — no ID resolution needed.
                self._proc = self.agent.resume_interactive(self.session_id, self.cwd)
            else:
                pre_spawn_id = self.agent.current_session_id(self.cwd)
                self._proc = self.agent.spawn_interactive(self.cwd)
                self._resolve_session_id(pre_spawn_id)
            self._proc.wait()
            resolved_id = self.agent.current_session_id(self.cwd)
            if resolved_id:
                self.db.update_session_agent_id(self._session_row_id, resolved_id)
        except KeyboardInterrupt:
            self._kill_proc()
            end_reason = "killed"
        except Exception as exc:
            self.logger.error("Agent launch error: %s", exc)
            end_reason = "error"
        finally:
            self._teardown(end_reason)

        return end_reason

    def _resolve_session_id(self, pre_spawn_id: str | None) -> None:
        """Poll briefly post-spawn until a new session ID appears, then record it."""
        for _ in range(20):  # up to 2s
            sid = self.agent.current_session_id(self.cwd)
            if sid and sid != pre_spawn_id:
                self.db.update_session_agent_id(self._session_row_id, sid)
                self.logger.debug("Session ID resolved: %s", sid)
                return
            time.sleep(0.1)
        self.logger.debug("Session ID not resolved within 2s after spawn")

    def _kill_proc(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _teardown(self, reason: str) -> None:
        if self._poller:
            self._poller.stop()
        if self._session_row_id >= 0:
            self.db.end_session(self._session_row_id, reason)
        self.logger.info("Session ended: %s reason=%s", self.agent.name, reason)

    @property
    def session_row_id(self) -> int:
        return self._session_row_id
