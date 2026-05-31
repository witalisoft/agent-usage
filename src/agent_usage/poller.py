"""Background polling thread — samples usage, detects crossings, fires scripts."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import UTC, datetime

import attrs

from agent_usage.agents.base import Agent, UsageFetchError
from agent_usage.config import AgentConfig
from agent_usage.db import Database


@attrs.define
class Poller:
    agent: Agent
    agent_cfg: AgentConfig
    db: Database
    session_row_id: int
    poll_interval: int
    logger: logging.Logger
    _stop_event: threading.Event = attrs.Factory(threading.Event)
    _thread: threading.Thread = attrs.field(init=False)
    # crossing state: metric → last percent seen
    _last_pct: dict[str, float] = attrs.Factory(dict)
    # script armed state: (metric, threshold) → bool (True = ready to fire)
    _script_armed: dict[tuple[str, float], bool] = attrs.Factory(dict)

    def __attrs_post_init__(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"poller-{self.agent.name}")
        # Arm all script rules
        for rule in self.agent_cfg.scripts or []:
            self._script_armed[(rule.metric, rule.threshold)] = True

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=self.poll_interval + 5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._tick()
            self._stop_event.wait(timeout=self.poll_interval)

    def _tick(self) -> None:
        try:
            usage = self.agent.get_usage()
        except UsageFetchError as exc:
            self.logger.warning("Poll tick skipped: %s", exc)
            return

        ts = datetime.now(tz=UTC).isoformat()
        self.logger.info("Sample %s %s", self.agent.name, usage)

        for metric, pct in usage.items():
            prev = self._last_pct.get(metric)
            self._check_script_crossings(metric, pct, prev, ts)
            self._last_pct[metric] = pct

        # Re-arm scripts where usage dropped back under threshold
        for rule in self.agent_cfg.scripts or []:
            pct = usage.get(rule.metric)
            if pct is not None and pct < rule.threshold:
                self._script_armed[(rule.metric, rule.threshold)] = True

    def _check_script_crossings(self, metric: str, pct: float, prev: float | None, ts: str) -> None:
        for rule in self.agent_cfg.scripts or []:
            if rule.metric != metric:
                continue
            key = (rule.metric, rule.threshold)
            armed = self._script_armed.get(key, True)
            if armed and (prev is None or prev < rule.threshold) and pct >= rule.threshold:
                self._fire_script(rule.cmd, metric, pct, rule.threshold, ts)
                self._script_armed[key] = False

    def _fire_script(self, cmd: str, metric: str, pct: float, threshold: float, ts: str) -> None:
        env = os.environ.copy()
        env.update(
            {
                "AGENT": self.agent.name,
                "LIMIT_NAME": metric,
                "PERCENT": f"{pct:.1f}",
                "THRESHOLD": f"{threshold:.1f}",
                "TIMESTAMP": ts,
            }
        )
        self.logger.info("Firing script for %s %s at %.1f%%: %s", self.agent.name, metric, pct, cmd)

        logger = self.logger

        def _wait() -> None:
            try:
                proc = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True, timeout=30)
                if proc.returncode != 0:
                    logger.warning(
                        "Script exited %d for %s %s: %s",
                        proc.returncode,
                        metric,
                        cmd,
                        (proc.stderr or proc.stdout).strip(),
                    )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Script timeout (30s) for %s %s: %s",
                    self.agent.name,
                    metric,
                    cmd,
                )

        threading.Thread(target=_wait, daemon=True).start()
