"""GitHub Copilot CLI provider."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import requests

from agent_usage.agents.base import Agent, SessionInfo, UsageFetchError

_PLANS: dict[str, int] = {
    "free": 50,
    "pro": 300,
    "pro+": 1500,
    "business": 300,
    "enterprise": 1000,
}


def _get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            t = result.stdout.strip()
            if t:
                return t
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise UsageFetchError("No GitHub token found. Set GITHUB_TOKEN or GH_TOKEN, or authenticate with 'gh auth login'.")


def _fetch_username(token: str) -> str:
    resp = requests.get(
        "https://api.github.com/user",
        headers=_gh_headers(token),
        timeout=15,
    )
    if resp.status_code != 200:
        raise UsageFetchError(f"GitHub /user error {resp.status_code}")
    login = resp.json().get("login")
    if not login:
        raise UsageFetchError("Could not determine GitHub username")
    return login


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }


def _session_db_path() -> Path:
    return Path.home() / ".copilot" / "session-store.db"


class CopilotProvider(Agent):
    name = "copilot"
    primary_metric = "monthly"

    def __init__(self, plan: str, limit: int | None = None) -> None:
        self._plan = plan
        try:
            self._limit = limit if limit is not None else _PLANS[plan]
        except KeyError as exc:
            raise ValueError(
                f"Unknown Copilot plan '{plan}'; known plans: {list(_PLANS.keys())}, exception: {exc}"
            ) from exc

    def detect(self) -> bool:
        return shutil.which("copilot") is not None

    def get_usage(self) -> dict[str, float]:
        token = _get_token()
        username = _fetch_username(token)

        now = datetime.now(tz=UTC)
        year = now.year
        month = str(now.month).zfill(2)

        path = f"/users/{username}/settings/billing/premium_request/usage?year={year}&month={month}"
        try:
            resp = requests.get(
                f"https://api.github.com{path}",
                headers=_gh_headers(token),
                timeout=15,
            )
        except requests.RequestException as exc:
            raise UsageFetchError(f"Copilot network error: {exc}") from exc

        if resp.status_code == 404:
            raise UsageFetchError(
                "Copilot billing endpoint 404 — token may lack Plan:read scope, "
                "or Copilot is billed via org/enterprise."
            )
        if resp.status_code != 200:
            raise UsageFetchError(f"Copilot billing API error {resp.status_code}")

        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise UsageFetchError(f"Copilot billing response is not valid JSON: {exc}") from exc
        items = data.get("usageItems") or []
        if len(items) == 0:
            raise UsageFetchError("No usage items found in Copilot billing response")
        total = sum(item.get("grossQuantity", 0) for item in items)
        pct = total / self._limit * 100 if self._limit else 0.0
        return {"monthly": round(pct, 2)}

    def current_session_id(self, cwd: str) -> str | None:
        db_path = _session_db_path()
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT id FROM sessions WHERE cwd=? ORDER BY updated_at DESC LIMIT 1",
                (cwd,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def list_sessions(self, cwd: str) -> list[SessionInfo]:
        """Return all sessions for the given cwd from the Copilot session DB, newest first."""
        db_path = _session_db_path()
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT id, updated_at, summary FROM sessions WHERE cwd=? ORDER BY updated_at DESC",
                (cwd,),
            ).fetchall()
            conn.close()
            result = []
            for row in rows:
                try:
                    dt = datetime.fromisoformat(row[1]).replace(tzinfo=UTC) if row[1] else None
                except Exception:
                    dt = None
                raw_summary = (row[2] or "").strip().replace("\n", " ")
                summary = raw_summary[:80] if raw_summary else None
                result.append(SessionInfo(session_id=row[0], updated_at=dt, summary=summary))
            return result
        except Exception:
            return []

    def spawn_interactive(self, cwd: str) -> subprocess.Popen:
        return subprocess.Popen(["copilot"], cwd=cwd)

    def resume_interactive(self, session_id: str, cwd: str) -> subprocess.Popen:
        """Resume a specific Copilot session using `copilot --resume=<session-id>`."""
        return subprocess.Popen(["copilot", f"--resume={session_id}"], cwd=cwd)
