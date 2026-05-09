"""Configuration loader for CodeGuardian.

Reads ``.codeguardian.yml`` from a given path (defaults to the current
working directory) and exposes a typed ``GuardianConfig`` object used by
the workflow, CLI, and batch processor to decide which agents to run,
which files to skip, and the minimum severity to report.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from codeguardian.agents.base import Severity

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILENAME = ".codeguardian.yml"

# Maps short agent names used in the YAML config to the attribute names
# on ``CodeReviewWorkflow`` and ``ReviewState``.
_AGENT_NAME_MAP: dict[str, str] = {
    "style": "style",
    "security": "security",
    "performance": "performance",
    "logic": "logic",
    "repo": "repo",
    "refactor": "refactor",
    "fix": "fix",
    "test": "test",
    "doc": "doc",
}

# Severity ordering for threshold comparison.
_SEVERITY_ORDER: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


@dataclass
class AgentConfig:
    """Per-agent configuration read from the YAML file."""

    enabled: bool = True
    severity_threshold: Optional[str] = None


@dataclass
class GuardianConfig:
    """Top-level configuration object loaded from ``.codeguardian.yml``."""

    model: Optional[str] = None
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)

    # ---- helpers ----

    def is_agent_enabled(self, agent_name: str) -> bool:
        """Return *True* if the given agent is enabled in the config."""
        cfg = self.agents.get(agent_name)
        return cfg.enabled if cfg is not None else True

    def get_severity_threshold(self, agent_name: str) -> Optional[Severity]:
        """Return the minimum severity for *agent_name*, or ``None`` if unset."""
        cfg = self.agents.get(agent_name)
        if cfg is None or cfg.severity_threshold is None:
            return None
        try:
            return Severity(cfg.severity_threshold.lower())
        except ValueError:
            logger.warning("Invalid severity_threshold '%s' for agent %s", cfg.severity_threshold, agent_name)
            return None

    def should_ignore(self, file_path: str) -> bool:
        """Return *True* if *file_path* matches any ignore pattern."""
        return any(fnmatch.fnmatch(file_path, pat) for pat in self.ignore)


def load_config(path: Optional[str | Path] = None) -> GuardianConfig:
    """Load ``.codeguardian.yml`` from *path* (or CWD) and return a ``GuardianConfig``.

    Returns a default (empty) config if the file does not exist or cannot
    be parsed, so callers never need to handle ``None``.
    """
    if path is None:
        config_path = Path.cwd() / _DEFAULT_CONFIG_FILENAME
    else:
        config_path = Path(path)

    if not config_path.is_file():
        logger.debug("No config file found at %s — using defaults", config_path)
        return GuardianConfig()

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse %s: %s — using defaults", config_path, exc)
        return GuardianConfig()

    if not isinstance(raw, dict):
        return GuardianConfig()

    model = raw.get("model")

    # Parse agents section
    agents: dict[str, AgentConfig] = {}
    raw_agents = raw.get("agents", {})
    if isinstance(raw_agents, dict):
        for name, value in raw_agents.items():
            if isinstance(value, dict):
                agents[name] = AgentConfig(
                    enabled=value.get("enabled", True),
                    severity_threshold=value.get("severity_threshold"),
                )
            elif isinstance(value, bool):
                agents[name] = AgentConfig(enabled=value)

    # Parse ignore list
    ignore: list[str] = []
    raw_ignore = raw.get("ignore", [])
    if isinstance(raw_ignore, list):
        ignore = [str(p) for p in raw_ignore]

    return GuardianConfig(model=model, agents=agents, ignore=ignore)
