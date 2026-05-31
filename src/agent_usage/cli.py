"""Console script entry point."""

from __future__ import annotations

import os
import signal
import sys

from rich.console import Console

from agent_usage import __version__
from agent_usage.paths import config_path, db_path, log_dir

console = Console()


def main() -> None:
    args = sys.argv[1:]

    if "--version" in args or "-V" in args:
        console.print(f"agent-usage {__version__}")
        return

    if "--paths" in args or "-p" in args:
        console.print(
            f"Path locations\n\nconfig path: {config_path()}\ndatabase path: {db_path()}\nlog directory: {log_dir()}",
            highlight=False,
        )
        return

    if "--help" in args or "-h" in args:
        console.print(
            f"[bold]agent-usage[/bold] {__version__}\n\n"
            "Monitor AI agent usage and launch agents.\n\n"
            "[bold]Flags:[/bold]\n"
            "  --version        Show version\n"
            "  --paths          Show path locations\n"
            "  --help           Show this help"
        )
        return

    _run_main()


def _run_main() -> None:
    from agent_usage.agents import detected_agents
    from agent_usage.config import agent_config, load_config
    from agent_usage.db import Database
    from agent_usage.logging_setup import setup_logger
    from agent_usage.menu import show_menu
    from agent_usage.paths import config_path, db_path
    from agent_usage.runner import Runner

    # Setup
    cwd = os.getcwd()

    # Auto-initialize config if missing
    cfg_path = config_path()
    if not cfg_path.exists():
        from pathlib import Path

        ref = Path(__file__).parent.parent.parent / "config.reference.yaml"
        if not ref.exists():
            ref = Path(__file__).parent.parent.parent.parent / "config.reference.yaml"
        if ref.exists():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(ref.read_text())

    cfg = load_config()
    db = Database(path=db_path())

    # Detect agents
    agents = detected_agents(cfg)

    # Show menu — pick agent or session
    choice = show_menu(agents, cfg, db, cwd)
    if choice is None:
        return

    chosen = choice.agent

    # Setup logger for this session
    logger, log_file = setup_logger(chosen.name)
    console.print(f"[dim]Logging to: {log_file}[/dim]")

    acfg = agent_config(cfg, chosen.name)
    poll_interval = acfg.poll_interval or cfg.poll_interval

    # Install SIGTERM/SIGINT handler for clean shutdown
    runner: Runner | None = None

    def _shutdown(signum, frame):  # type: ignore[no-untyped-def]
        if runner and runner.session_row_id >= 0:
            db.end_session(runner.session_row_id, "killed")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    runner = Runner(
        agent=chosen,
        agent_cfg=acfg,
        db=db,
        logger=logger,
        poll_interval=poll_interval,
        cwd=cwd,
        session_id=choice.session_id,
    )

    runner.run()

    db.close()
