"""Tests for config loader."""

from pathlib import Path

import yaml

from agent_usage.config import (
    load_config,
)


def test_defaults_only(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg.poll_interval == 60


def test_deep_merge_override(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"poll_interval": 30}))
    cfg = load_config(cfg_file)
    assert cfg.poll_interval == 30


def test_empty_config_valid(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("")
    cfg = load_config(cfg_file)
    assert cfg.poll_interval == 60


def test_script_rule_parsed(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        yaml.dump({"agents": {"claude": {"scripts": [{"metric": "7day", "threshold": 80, "cmd": "echo hi"}]}}})
    )
    cfg = load_config(cfg_file)
    assert len(cfg.agents["claude"].scripts) == 1
    rule = cfg.agents["claude"].scripts[0]
    assert rule.metric == "7day"
    assert rule.threshold == 80


def test_reference_yaml_parses():
    ref = Path(__file__).parent.parent / "config.reference.yaml"
    assert ref.exists(), "config.reference.yaml not found"
    raw = yaml.safe_load(ref.read_text())
    assert isinstance(raw, dict)
    assert "poll_interval" in raw
    assert "agents" in raw
