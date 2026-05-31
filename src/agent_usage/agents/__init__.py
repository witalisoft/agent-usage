"""Agent registry."""

from __future__ import annotations

from agent_usage.agents.base import Agent
from agent_usage.agents.claude import ClaudeProvider
from agent_usage.agents.codex import CodexProvider
from agent_usage.agents.copilot import CopilotProvider
from agent_usage.config import Config, agent_config

_REGISTRY: dict[str, type[Agent]] = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
    "copilot": CopilotProvider,
}


def build_agents(cfg: Config) -> list[Agent]:
    """Instantiate all registered agents, passing per-agent config where needed."""
    agents: list[Agent] = []
    for name, cls in _REGISTRY.items():
        acfg = agent_config(cfg, name)
        if name == "copilot":
            plan = acfg.plan or "pro"
            limit = acfg.limit
            instance = CopilotProvider(plan=plan, limit=limit)
        else:
            instance = cls()  # type: ignore[call-arg]
        agents.append(instance)
    return agents


def detected_agents(cfg: Config) -> list[Agent]:
    """Return agents that are installed on this system."""
    return [a for a in build_agents(cfg) if a.detect()]
