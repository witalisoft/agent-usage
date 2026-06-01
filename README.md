# agent-usage

<p align="center">
<img width="671" height="167" alt="image" src="https://github.com/user-attachments/assets/a3169f04-537e-4114-bd89-31472f323bdd" />
</p>

A single command-line tool to discover installed AI coding agents, monitor the subscription usage of each one, list all sessions from the current directory, and trigger a custom script when usage exceeds a defined threshold.

## Why it exists

Developers today often run multiple AI coding agents side by side — Claude Code, GitHub Copilot CLI, OpenAI Codex, and others — each with its own subscription model, quota reset window, and usage dashboard. Keeping track of how much of each quota you have consumed requires logging into several different websites or running provider-specific commands. Past sessions are scattered across agent-specific storage with no unified way to browse them.

`agent-usage` was built to solve these problems in one place:

1. **Discover** — automatically detect which AI agents are installed on the current machine, with no manual configuration required.
2. **Monitor** — poll each agent's usage API in the background and show a unified view of how much of each subscription has been consumed across all relevant time windows (hourly, daily, weekly, monthly).
3. **Browse sessions** — list all agent sessions that were started in the current working directory, across every discovered agent, so you can resume or inspect past work without digging into each tool's own history UI.
4. **Act** — fire a user-defined shell command (notify, block, switch agents, log, etc.) the moment a metric crosses a configured threshold, so you never hit a hard limit mid-session without warning.

## Supported agents

| Agent | Metrics tracked |
|---|---|
| Claude Code | 5-hour window, 7-day rolling |
| GitHub Copilot CLI | Monthly AI credits requests |
| OpenAI Codex | 5-hour window, weekly |

## Authentication

Each agent must be authenticated before `agent-usage` can retrieve its usage data.

| Agent | Authentication |
|---|---|
| Claude Code | `claude login` |
| GitHub Copilot CLI | `gh auth login` (see note below) |
| OpenAI Codex | `codex login` |

**GitHub Copilot CLI — extended scope required**

The default `gh auth login` flow does not request the `user` scope, which is needed to read Copilot seat and usage data. After logging in (or if you are already logged in), run:

```bash
gh auth refresh -s user
```

This adds the `user` scope to your existing token without requiring a full re-login.

## Installation

**Quick run (no install):**

```bash
uvx --from git+https://github.com/witalisoft/agent-usage agent-usage
```

Requires Python 3.13+. Install permanently with [uv](https://github.com/astral-sh/uv):

```bash
uv tool install git+https://github.com/witalisoft/agent-usage
```

Or in a virtual environment:

```bash
uv sync
source .venv/bin/activate
agent-usage
```

## Usage

```
agent-usage [--version] [--help]
```

Running without flags opens an interactive menu where you can:

- View current usage for all detected agents
- Browse all sessions started in the current working directory, across every discovered agent
- Launch an agent in the current working directory
- Hand off the active session to a different agent

## Configuration

To see the exact locations of the config file, database, and other components on your system, run:

```bash
agent-usage --paths
```

On first run, `agent-usage` creates a `config.yaml` in your platform config directory. Edit it to set thresholds and scripts. A full reference of every available option is provided in `config.reference.yaml`.

**Example — send a desktop notification when GitHub Copilot CLI reaches 80 % of monthly quota:**

```yaml
agents:
  copilot:
    plan: pro
    scripts:
      - metric: monthly
        threshold: 80
        cmd: "notify-send 'Copilot ${PERCENT}% of ${LIMIT_NAME}'"
```

The following environment variables are injected into every script command:

| Variable | Description |
|---|---|
| `AGENT` | Agent name (e.g. `copilot`) |
| `METRIC` | Metric name (e.g. `monthly`) |
| `PERCENT` | Current usage percentage (0–100) |
| `THRESHOLD` | The configured threshold that was crossed |
| `TIMESTAMP` | ISO-8601 timestamp of the trigger |

Scripts fire once when a metric crosses the threshold and re-arm automatically when usage drops back below it.

## Development

```bash
uv sync --group dev
source .venv/bin/activate
python -m pytest
```

Lint and format:

```bash
ruff check .
ruff format .
```

## Platform support

Tested on macOS and Linux.
