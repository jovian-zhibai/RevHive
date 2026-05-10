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

    MODEL_PRESETS: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "mimo": {"base_url": "https://token-plan-cn.xiaomimimo.com/v1", "model": "mimo-v2.5-pro"},
        "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
        "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
        "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
        "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4"},
        "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
        "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-20250514", "provider": "anthropic"},
        "claude": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-20250514", "provider": "anthropic"},
    })

    model: Optional[str] = None
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    ignore: list[str] = field(default_factory=list)

    # ---- helpers ----

    def resolve_preset(self, model_name: Optional[str]) -> dict[str, str]:
        """Resolve a model name against MODEL_PRESETS.

        If *model_name* matches a preset key, return the preset dict
        (with ``base_url``, ``model``, and optionally ``provider``).
        Otherwise return an empty dict — the caller falls through to
        env-var / default logic.
        """
        if model_name and model_name in self.MODEL_PRESETS:
            return dict(self.MODEL_PRESETS[model_name])
        return {}

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
        """Return *True* if *file_path* matches any ignore pattern.

        Patterns containing ``**`` are handled manually: ``**`` matches any
        number of directory levels.  All other patterns use
        :func:`fnmatch.fnmatch`.
        """
        for pat in self.ignore:
            if "**" in pat:
                prefix = pat.split("**")[0].rstrip("/").rstrip("*")
                suffix = pat.split("**")[-1].lstrip("/")
                if prefix:
                    if file_path == prefix or file_path.startswith(prefix + "/"):
                        return True
                if suffix:
                    if file_path.endswith(suffix):
                        return True
                if not prefix and not suffix:
                    return True
            elif fnmatch.fnmatch(file_path, pat):
                return True
        return False


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
