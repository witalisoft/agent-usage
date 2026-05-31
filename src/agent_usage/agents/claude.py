"""Claude Code provider."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

from agent_usage.agents.base import Agent, SessionInfo, UsageFetchError

_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
_API_BASE = "https://api.anthropic.com"
_USAGE_ENDPOINT = "/api/oauth/usage"

# OAuth token refresh — mirrors the Codex proactive-refresh pattern.
# Endpoint and client ID extracted from the Claude Code binary.
_OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

_BACKOFF_BASE = 300
_BACKOFF_MAX = 3600


def _cwd_slug(cwd: str) -> str:
    """Convert absolute path to Claude projects dir slug."""
    return cwd.replace("/", "-")


def _load_credentials() -> dict[str, str] | None:
    """Load OAuth credentials from the credentials file.

    Returns the ``claudeAiOauth`` dict, or ``None`` if the file is absent or
    malformed.  Does *not* fall back to the Keychain — callers that need
    Keychain access should call ``_load_keychain_credentials()`` directly.
    """
    if _CREDENTIALS_PATH.exists():
        try:
            with open(_CREDENTIALS_PATH) as fh:
                data = json.load(fh)
            return data.get("claudeAiOauth") or None
        except Exception:
            pass
    return None


def _load_keychain_credentials() -> dict[str, str] | None:
    """Load OAuth credentials from the macOS Keychain (macOS only).

    Returns the ``claudeAiOauth`` dict from the ``Claude Code-credentials``
    Keychain entry, or ``None`` on any failure or non-macOS platform.
    """
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout.strip())
        return data.get("claudeAiOauth") or None
    except Exception:
        return None


def _save_credentials(creds: dict[str, str]) -> None:
    """Persist updated credentials back to the credentials file (best-effort).

    Writes atomically via a temp file so a crash mid-write does not corrupt the
    existing credentials.
    """
    try:
        data: dict = {}
        if _CREDENTIALS_PATH.exists():
            with open(_CREDENTIALS_PATH) as fh:
                data = json.load(fh)
        data["claudeAiOauth"] = creds
        tmp = _CREDENTIALS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_CREDENTIALS_PATH)
    except Exception:
        pass  # Non-fatal — the refreshed token is still used in memory.


def _save_keychain_credentials(creds: dict[str, str]) -> None:
    """Persist refreshed credentials back to the macOS Keychain (best-effort).

    Reads the existing Keychain entry to discover the account name and the
    full JSON blob (to preserve any top-level keys beyond ``claudeAiOauth``),
    merges the updated credentials, then writes the entry back in-place via
    ``security add-generic-password -U``.

    No-op on non-macOS platforms or if no Keychain entry exists.
    """
    if platform.system() != "Darwin":
        return
    try:
        # Discover the account name from the existing Keychain entry.
        # ``security find-generic-password`` without ``-w`` prints metadata
        # to stdout including a line like: "acct"<blob>="user@example.com"
        meta = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
            ],
            capture_output=True,
            text=True,
        )
        account = ""
        for line in meta.stdout.splitlines():
            if '"acct"' in line:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    account = parts[1].strip().strip('"')
                break

        # Re-read the full JSON blob to preserve any other top-level keys.
        pw_result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(pw_result.stdout.strip())
        data["claudeAiOauth"] = creds

        # ``-U`` updates an existing entry rather than creating a duplicate.
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                "Claude Code-credentials",
                "-a",
                account,
                "-w",
                json.dumps(data),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        pass  # Non-fatal — refreshed token is still used in memory.


def _is_expired(creds: dict[str, str]) -> bool:
    """Return True when the access token is within 5 minutes of its expiry."""
    expires_at = float(creds.get("expiresAt", 0))
    buffer_ms = 5 * 60 * 1000
    return (time.time() * 1000) >= (expires_at - buffer_ms)


def _refresh_access_token(creds: dict[str, str]) -> dict[str, str]:
    """Obtain a fresh access token via the OAuth refresh-token grant.

    Mirrors the Codex ``refresh_tokens()`` pattern: POST
    ``grant_type=refresh_token`` to Anthropic's token endpoint, update only
    the fields that were returned, and hand back the mutated credentials dict.

    Parameters:
        creds: The current ``claudeAiOauth`` credentials dict.

    Returns:
        Updated credentials dict with a fresh ``accessToken`` (and optionally
        a rotated ``refreshToken`` and new ``expiresAt``).

    Raises:
        UsageFetchError: When no refresh token is present or the HTTP request
            fails.
    """
    refresh_token = creds.get("refreshToken")
    if not refresh_token:
        raise UsageFetchError("No Claude refresh token available; run 'claude' to re-authenticate")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _OAUTH_CLIENT_ID,
    }
    try:
        resp = requests.post(
            _OAUTH_TOKEN_URL,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "claude-code/2.0.37"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise UsageFetchError(f"Claude token refresh failed (HTTP {resp.status_code}): {resp.text}")
        data = resp.json()
    except requests.RequestException as exc:
        raise UsageFetchError(f"Claude token refresh request failed: {exc}") from exc

    # Update only the fields that were returned (mirrors Codex persist_tokens pattern).
    if "access_token" in data:
        creds["accessToken"] = data["access_token"]
    if "refresh_token" in data:
        creds["refreshToken"] = data["refresh_token"]
    if "expires_in" in data:
        # expires_in is in seconds; expiresAt is stored in milliseconds.
        creds["expiresAt"] = str((time.time() + float(data["expires_in"])) * 1000)

    return creds


def _ensure_fresh_credentials() -> dict[str, str]:
    """Load credentials, refresh proactively if expired, persist, and return.

    Refresh order (mirrors Codex ``ensure_fresh_auth``, extended for Keychain):
    1. Load from file; if not expired return immediately.
    2. If expired: attempt OAuth refresh via the file's ``refreshToken``.
    3. If that fails: attempt OAuth refresh via the Keychain's ``refreshToken``
       (macOS only — the Keychain may hold a newer token after a rotation).
    4. If all strategies fail: raise ``UsageFetchError``.

    On every successful refresh the updated credentials are persisted back to
    both the file and — when running on macOS — the Keychain, so the two
    stores stay in sync and Claude Code itself picks up the new tokens.

    Returns:
        Fresh ``claudeAiOauth`` credentials dict with a valid ``accessToken``.

    Raises:
        UsageFetchError: When credentials cannot be loaded or refreshed.
    """
    file_creds = _load_credentials()
    creds = file_creds or _load_keychain_credentials()
    if not creds:
        raise UsageFetchError("No Claude credentials found; run 'claude' to authenticate")

    # Track whether the initial credentials came solely from the Keychain so
    # we know to write refreshed tokens back there as well as to the file.
    from_keychain = file_creds is None

    if not _is_expired(creds):
        return creds

    # Token expired — try OAuth refresh with the loaded credentials first.
    try:
        refreshed = _refresh_access_token(dict(creds))
        _save_credentials(refreshed)
        if from_keychain:
            _save_keychain_credentials(refreshed)
        return refreshed
    except UsageFetchError:
        pass

    # Loaded refresh token invalid/rotated — try the Keychain's refresh token
    # explicitly (the Keychain may hold a newer token after a rotation).
    keychain_creds = _load_keychain_credentials()
    if keychain_creds:
        # Use Keychain directly if its access token is still valid.
        if not _is_expired(keychain_creds):
            _save_credentials(keychain_creds)
            _save_keychain_credentials(keychain_creds)
            return keychain_creds
        # Keychain access token also expired; try its refresh token.
        try:
            refreshed = _refresh_access_token(dict(keychain_creds))
            _save_credentials(refreshed)
            _save_keychain_credentials(refreshed)
            return refreshed
        except UsageFetchError:
            pass

    raise UsageFetchError("Claude token expired and refresh failed; run 'claude' to re-authenticate")


def _auth_headers(creds: dict[str, str]) -> dict[str, str]:
    """Build Authorization headers for the Anthropic usage API."""
    return {
        "Authorization": f"Bearer {creds['accessToken']}",
        "Content-Type": "application/json",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.0.37",
    }


def _read_first_user_message(path: Path) -> str | None:
    """Extract the first real user message from a Claude JSONL session file.

    Skips system-injected lines whose content starts with ``<`` (e.g.
    ``<local-command-caveat>``, ``<command-name>``).  Returns the message
    truncated to 80 characters, or ``None`` if none found.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                    if obj.get("type") != "user":
                        continue
                    content = (obj.get("message") or {}).get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text and not text.startswith("<"):
                                    return text.replace("\n", " ")[:80]
                    elif isinstance(content, str) and content and not content.startswith("<"):
                        return content.replace("\n", " ")[:80]
                except Exception:
                    pass
    except Exception:
        pass
    return None


class ClaudeProvider(Agent):
    name = "claude"
    primary_metric = "5h"

    def __init__(self) -> None:
        self._consecutive_429s = 0
        self._backoff_until = 0.0

    def detect(self) -> bool:
        return shutil.which("claude") is not None

    def get_usage(self) -> dict[str, float]:
        creds = _ensure_fresh_credentials()

        if time.time() < self._backoff_until:
            raise UsageFetchError(f"Claude API in backoff for {self._backoff_until - time.time():.0f}s")

        headers = _auth_headers(creds)
        url = f"{_API_BASE}{_USAGE_ENDPOINT}"

        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    self._consecutive_429s = 0
                    self._backoff_until = 0.0
                    return _parse_usage(resp.json())
                elif resp.status_code == 401:
                    raise UsageFetchError("Claude token expired (401); run 'claude' to refresh")
                elif resp.status_code == 429:
                    if attempt < 2:
                        time.sleep(4 * (2**attempt))
                        continue
                    duration = min(_BACKOFF_BASE * (2**self._consecutive_429s), _BACKOFF_MAX)
                    self._backoff_until = time.time() + duration
                    self._consecutive_429s += 1
                    raise UsageFetchError("Claude API rate limited (429)")
                else:
                    raise UsageFetchError(f"Claude API error {resp.status_code}")
            except requests.RequestException as exc:
                raise UsageFetchError(f"Claude network error: {exc}") from exc

        raise UsageFetchError("Claude API rate limited after retries")

    def current_session_id(self, cwd: str) -> str | None:
        slug = _cwd_slug(cwd)
        project_dir = Path.home() / ".claude" / "projects" / slug
        if not project_dir.exists():
            return None
        jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not jsonl_files:
            return None
        return jsonl_files[0].stem

    def list_sessions(self, cwd: str) -> list[SessionInfo]:
        """Return all sessions for the given cwd, newest first, by scanning project JSONL files."""
        slug = _cwd_slug(cwd)
        project_dir = Path.home() / ".claude" / "projects" / slug
        if not project_dir.exists():
            return []
        jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            SessionInfo(
                session_id=f.stem,
                updated_at=datetime.fromtimestamp(f.stat().st_mtime, tz=UTC),
                summary=_read_first_user_message(f),
            )
            for f in jsonl_files
        ]

    def spawn_interactive(self, cwd: str) -> subprocess.Popen:
        return subprocess.Popen(["claude"], cwd=cwd)

    def resume_interactive(self, session_id: str, cwd: str) -> subprocess.Popen:
        """Resume a specific Claude session using `claude -r <session-id>`."""
        return subprocess.Popen(["claude", "-r", session_id], cwd=cwd)


def _parse_usage(data: dict) -> dict[str, float]:
    result: dict[str, float] = {}
    five_hour = data.get("five_hour") or {}
    if (util := five_hour.get("utilization")) is not None:
        result["5h"] = float(util)
    seven_day = data.get("seven_day") or {}
    if (util := seven_day.get("utilization")) is not None:
        result["7day"] = float(util)
    seven_day_opus = data.get("seven_day_opus") or {}
    if (util := seven_day_opus.get("utilization")) is not None:
        result["7day_opus"] = float(util)
    if not result:
        raise UsageFetchError("Claude usage response has no utilization fields")
    return result
