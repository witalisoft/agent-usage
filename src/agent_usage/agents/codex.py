"""Codex (OpenAI) provider."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import requests

from agent_usage.agents.base import Agent, SessionInfo, UsageFetchError

_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
_USAGE_PATH = "/wham/usage"
_REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_TOKEN_REFRESH_INTERVAL_DAYS = 8


def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


def _decode_jwt_payload(jwt: str) -> dict:
    parts = jwt.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _jwt_expiry(jwt: str) -> datetime | None:
    try:
        claims = _decode_jwt_payload(jwt)
        exp = claims.get("exp")
        if exp is not None:
            return datetime.fromtimestamp(exp, tz=UTC)
    except Exception:
        pass
    return None


def _needs_refresh(auth: dict) -> bool:
    tokens = auth.get("tokens") or {}
    access_token = tokens.get("access_token", "")
    if access_token:
        expires_at = _jwt_expiry(access_token)
        if expires_at is not None:
            return expires_at <= datetime.now(tz=UTC)
    last_refresh_str = auth.get("last_refresh")
    if not last_refresh_str:
        return False
    try:
        last_refresh = datetime.fromisoformat(last_refresh_str.replace("Z", "+00:00"))
        age_days = (datetime.now(tz=UTC) - last_refresh).total_seconds() / 86400
        return age_days >= _TOKEN_REFRESH_INTERVAL_DAYS
    except Exception:
        return False


def _refresh_tokens(auth: dict, auth_path: Path) -> dict:
    tokens = auth.get("tokens") or {}
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise UsageFetchError("No refresh_token in codex auth.json; run 'codex login'")

    endpoint = os.environ.get("CODEX_REFRESH_TOKEN_URL_OVERRIDE", _REFRESH_TOKEN_URL)
    payload = json.dumps(
        {
            "client_id": _CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )
    try:
        resp = requests.post(endpoint, data=payload, headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code != 200:
            raise UsageFetchError(f"Codex token refresh failed HTTP {resp.status_code}")
        refreshed = resp.json()
    except requests.RequestException as exc:
        raise UsageFetchError(f"Codex token refresh network error: {exc}") from exc

    for key in ("access_token", "refresh_token", "id_token"):
        if key in refreshed:
            tokens[key] = refreshed[key]
    auth["tokens"] = tokens
    auth["last_refresh"] = datetime.now(tz=UTC).isoformat()

    tmp = auth_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(auth, indent=2), encoding="utf-8")
    tmp.replace(auth_path)
    return auth


def _load_auth() -> dict:
    auth_path = _codex_home() / "auth.json"
    if not auth_path.exists():
        raise UsageFetchError("codex auth.json not found; run 'codex login'")
    with open(auth_path) as fh:
        auth = json.load(fh)
    if _needs_refresh(auth):
        auth = _refresh_tokens(auth, auth_path)
    return auth


def _state_db_path() -> Path | None:
    """Find ~/.codex/state_*.sqlite (take newest by mtime)."""
    home = _codex_home()
    dbs = sorted(home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    return dbs[0] if dbs else None


class CodexProvider(Agent):
    name = "codex"
    primary_metric = "5h"

    def detect(self) -> bool:
        return shutil.which("codex") is not None

    def get_usage(self) -> dict[str, float]:
        auth = _load_auth()
        tokens = auth.get("tokens") or {}
        access_token = tokens.get("access_token")
        if not access_token:
            raise UsageFetchError("No access_token in codex auth.json; requires ChatGPT login")

        base_url = os.environ.get("CODEX_CHATGPT_BASE_URL", _DEFAULT_BASE_URL)
        url = base_url.rstrip("/") + _USAGE_PATH
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "codex-cli",
            "Accept": "application/json",
        }
        account_id = tokens.get("account_id")
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id

        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise UsageFetchError(f"Codex usage API error {resp.status_code}")
            return _parse_usage(resp.json())
        except requests.RequestException as exc:
            raise UsageFetchError(f"Codex network error: {exc}") from exc

    def current_session_id(self, cwd: str) -> str | None:
        db_path = _state_db_path()
        if not db_path:
            return None
        try:
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT id FROM threads WHERE cwd=? ORDER BY updated_at DESC LIMIT 1",
                (cwd,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def list_sessions(self, cwd: str) -> list[SessionInfo]:
        """Return all sessions for the given cwd from the Codex state DB, newest first."""
        db_path = _state_db_path()
        if not db_path:
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT id, updated_at, title, first_user_message FROM threads WHERE cwd=? ORDER BY updated_at DESC",
                (cwd,),
            ).fetchall()
            conn.close()
            result = []
            for row in rows:
                try:
                    dt = datetime.fromtimestamp(int(row[1]), tz=UTC) if row[1] else None
                except Exception:
                    dt = None
                # Prefer title; fall back to first_user_message; truncate to 80 chars.
                raw_summary = (row[2] or row[3] or "").strip().replace("\n", " ")
                summary = raw_summary[:80] if raw_summary else None
                result.append(SessionInfo(session_id=row[0], updated_at=dt, summary=summary))
            return result
        except Exception:
            return []

    def spawn_interactive(self, cwd: str) -> subprocess.Popen:
        return subprocess.Popen(["codex"], cwd=cwd)

    def resume_interactive(self, session_id: str, cwd: str) -> subprocess.Popen:
        """Resume a specific Codex session using `codex resume <session-id>`."""
        return subprocess.Popen(["codex", "resume", session_id], cwd=cwd)


def _parse_usage(data: dict) -> dict[str, float]:
    rate_limit = data.get("rate_limit") or {}
    result: dict[str, float] = {}

    primary = rate_limit.get("primary_window") or {}
    if (pct := primary.get("used_percent")) is not None:
        result["5h"] = float(pct)

    secondary = rate_limit.get("secondary_window") or {}
    if (pct := secondary.get("used_percent")) is not None:
        result["weekly"] = float(pct)

    if not result:
        raise UsageFetchError("Codex usage response has no used_percent fields")
    return result
