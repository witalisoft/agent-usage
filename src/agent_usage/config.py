"""Configuration loading, validation, and typed models."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import attrs
import yaml


class ConfigError(ValueError):
    pass


@attrs.define
class ScriptRule:
    metric: str
    threshold: float
    cmd: str


@attrs.define
class AgentConfig:
    poll_interval: int | None = None
    scripts: list[ScriptRule] = attrs.Factory(list)
    # copilot-specific
    plan: str | None = None
    limit: int | None = None


@attrs.define
class Config:
    poll_interval: int = 60
    agents: dict[str, AgentConfig] = attrs.Factory(dict)


_KNOWN_METRICS: dict[str, list[str]] = {
    "claude": ["5h", "7day"],
    "codex": ["5h", "weekly"],
    "copilot": ["monthly"],
}


def _default_raw() -> dict[str, Any]:
    return {
        "poll_interval": 60,
        "agents": {
            "claude": {
                "scripts": [],
            },
            "codex": {
                "scripts": [],
            },
            "copilot": {
                "plan": "pro",
                "scripts": [],
            },
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    result = copy.deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _parse_script_rule(raw: Any, agent: str, idx: int) -> ScriptRule:
    if not isinstance(raw, dict):
        raise ConfigError(f"agents.{agent}.scripts[{idx}]: must be a mapping")
    for field in ("metric", "threshold", "cmd"):
        if field not in raw:
            raise ConfigError(f"agents.{agent}.scripts[{idx}]: missing '{field}'")
    metric = str(raw["metric"])
    known = _KNOWN_METRICS.get(agent, [])
    if known and metric not in known:
        raise ConfigError(f"agents.{agent}.scripts[{idx}].metric '{metric}' unknown; known: {known}")
    threshold = float(raw["threshold"])
    if not (0 <= threshold <= 100):
        raise ConfigError(f"agents.{agent}.scripts[{idx}].threshold {threshold} not in 0-100")
    return ScriptRule(metric=metric, threshold=threshold, cmd=str(raw["cmd"]))


def _parse_agent_config(raw: dict[str, Any], name: str) -> AgentConfig:
    cfg = AgentConfig()

    if "poll_interval" in raw:
        cfg.poll_interval = int(raw["poll_interval"])

    if "scripts" in raw:
        scripts_raw = raw["scripts"]
        if not isinstance(scripts_raw, list):
            raise ConfigError(f"agents.{name}.scripts must be a list")
        cfg.scripts = [_parse_script_rule(s, name, i) for i, s in enumerate(scripts_raw)]

    if "plan" in raw:
        cfg.plan = str(raw["plan"])
    if "limit" in raw:
        cfg.limit = int(raw["limit"])

    return cfg


def _parse_merged(merged: dict[str, Any]) -> Config:
    cfg = Config()

    if "poll_interval" in merged:
        cfg.poll_interval = int(merged["poll_interval"])

    if "agents" in merged:
        agents_raw = merged["agents"]
        if not isinstance(agents_raw, dict):
            raise ConfigError("agents must be a mapping")
        cfg.agents = {name: _parse_agent_config(araw, name) for name, araw in agents_raw.items()}

    return cfg


def load_config(config_path: Path | None = None) -> Config:
    """Load, merge, and validate config. Returns typed Config."""
    merged = _default_raw()

    if config_path is None:
        from agent_usage.paths import config_path as default_config_path

        config_path = default_config_path()

    if config_path.exists():
        with open(config_path) as fh:
            user_raw = yaml.safe_load(fh) or {}
        if not isinstance(user_raw, dict):
            raise ConfigError("config.yaml must be a YAML mapping")
        merged = _deep_merge(merged, user_raw)

    return _parse_merged(merged)


def agent_config(cfg: Config, name: str) -> AgentConfig:
    """Return per-agent config, falling back to defaults for unknown agents."""
    if name in cfg.agents:
        return cfg.agents[name]
    return AgentConfig()
