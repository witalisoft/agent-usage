"""InquirerPy fuzzy selection menu — arrow-key navigation + type-to-filter."""

from __future__ import annotations

from datetime import UTC, datetime

import attrs
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from rich.console import Console

from agent_usage.agents.base import Agent, SessionInfo, UsageFetchError
from agent_usage.config import Config
from agent_usage.db import Database

console = Console()


@attrs.define
class MenuChoice:
    """Represents the user's selection from the menu."""

    agent: Agent
    session_id: str | None = None  # None = start new session, str = resume existing


def _format_age(dt: datetime | None) -> str:
    """Format a datetime as a human-readable age string (e.g. '2h ago')."""
    if not dt:
        return "never"
    try:
        delta = datetime.now(tz=UTC) - dt.replace(tzinfo=UTC) if dt.tzinfo is None else datetime.now(tz=UTC) - dt
        minutes = int(delta.total_seconds() / 60)
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except Exception:
        return "unknown"


def _format_last_used(last_used_iso: str | None) -> str:
    """Format an ISO-8601 timestamp as a human-readable age string."""
    if not last_used_iso:
        return "never"
    try:
        dt = datetime.fromisoformat(last_used_iso).replace(tzinfo=UTC)
        return _format_age(dt)
    except Exception:
        return last_used_iso[:10]


def _bar(pct: float, width: int = 12) -> str:
    """Build a fixed-width ASCII progress bar string."""
    filled = min(int(pct / 100.0 * width), width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _metrics_str(usage: dict[str, float]) -> str:
    """Render usage metrics as a single string of bar+label blocks."""
    if not usage:
        return "(usage unavailable)"
    return "  |  ".join(f"{_bar(p)} {m} {p:.0f}%" for m, p in usage.items())


def _short_id(session_id: str) -> str:
    """Shorten a session ID to 8 chars + ellipsis."""
    return session_id[:8] + "…" if len(session_id) > 8 else session_id


def show_menu(agents: list[Agent], cfg: Config, db: Database, cwd: str) -> MenuChoice | None:
    """Display an arrow-key selection menu; return MenuChoice or None if cancelled."""
    if not agents:
        console.print("[yellow]No agents detected on this system.[/yellow]")
        return None

    # Fetch usage and sessions for each agent upfront.
    usage_map: dict[str, dict[str, float]] = {}
    sessions_map: dict[str, list[SessionInfo]] = {}
    for agent in agents:
        try:
            usage_map[agent.name] = agent.get_usage()
        except UsageFetchError as exc:
            console.print(f"[dim]Warning: could not fetch {agent.name} usage: {exc}[/dim]")
            usage_map[agent.name] = {}
        sessions_map[agent.name] = agent.list_sessions(cwd)

    # ── Agents section ────────────────────────────────────────────────────────
    # Pre-compute all column values so we can measure and pad uniformly.
    agent_name_w = max(len(a.name.upper()) for a in agents)
    agent_rows = []
    for agent in agents:
        metrics = _metrics_str(usage_map[agent.name])
        sessions_30d = db.sessions_last_n_days(agent.name, 30)
        last_used = _format_last_used(db.last_used(agent.name))
        agent_rows.append((agent, metrics, sessions_30d, last_used))

    metrics_w = max(len(r[1]) for r in agent_rows)
    sess_count_w = max(len(str(r[2])) for r in agent_rows)

    choices: list = [{"name": "── Agents ──", "value": None, "disabled": ""}]

    for agent, metrics, sessions_30d, last_used in agent_rows:
        name_col = agent.name.upper().ljust(agent_name_w)
        metrics_col = metrics.ljust(metrics_w)
        count_col = str(sessions_30d).rjust(sess_count_w)
        title = f"{name_col}  {metrics_col}  │  {count_col} sess/30d  last: {last_used}"
        mc = MenuChoice(agent=agent, session_id=None)
        choices.append(Choice(value=mc, name=title))

    # ── Sessions section ──────────────────────────────────────────────────────
    all_sessions = [(agent, info) for agent in agents for info in sessions_map[agent.name]]
    if all_sessions:
        badge_w = max(len(f"[{agent.name}]") for agent, _ in all_sessions)

        choices.append({"name": "── Sessions (this directory) ──", "value": None, "disabled": ""})
        for agent, info in all_sessions:
            badge = f"[{agent.name}]".ljust(badge_w)
            sid = _short_id(info.session_id)
            age = _format_age(info.updated_at).ljust(10)
            summary = f'  "{info.summary}"' if info.summary else ""
            title = f"{badge}  {sid}  {age}{summary}"
            mc = MenuChoice(agent=agent, session_id=info.session_id)
            choices.append(Choice(value=mc, name=title))

    try:
        return inquirer.fuzzy(
            message="Pick an agent or session (↑↓ or type to filter):",
            choices=choices,
            max_height="70%",
            mandatory=False,
        ).execute()
    except KeyboardInterrupt:
        return None
