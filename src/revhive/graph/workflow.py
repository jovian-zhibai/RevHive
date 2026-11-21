"""LangGraph workflow for multi-agent code review.

Default backend: MiMo (https://api.xiaomimimo.com/v1).
"""

import asyncio
import glob
import importlib.util
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from revhive.agents import (
    CoordinatorAgent,
    DocAgent,
    FixAgent,
    LogicAgent,
    PerformanceAgent,
    RefactorAgent,
    RepoAgent,
    SecurityAgent,
    StyleAgent,
    TestAgent,
)
from revhive.agents.base import SEVERITY_ORDER, AgentResult, BaseReviewAgent
from revhive.config import RevHiveConfig, load_config

logger = logging.getLogger(__name__)

#: Below this many seconds left, an agent cannot finish a useful pass.
_MIN_AGENT_SECONDS = 25.0

# Agent class registry — maps YAML agent names to their class.
# To add a new agent: (1) add entry here, (2) add a state field below.
_AGENT_CLASSES: dict[str, type] = {
    "style":       StyleAgent,
    "security":    SecurityAgent,
    "performance": PerformanceAgent,
    "logic":       LogicAgent,
    "repo":        RepoAgent,
    "refactor":    RefactorAgent,
    "fix":         FixAgent,
    "test":        TestAgent,
    "doc":         DocAgent,
}


def load_plugins(plugin_dir: str = "plugins", allowed_plugins: set[str] | None = None) -> dict[str, type]:
    """Load custom agent plugins from a directory.

    Each .py file in *plugin_dir* can define an agent class that extends
    :class:`BaseReviewAgent`. The class must be named ``Agent`` or have
    a ``PLUGIN_NAME`` attribute specifying the registry name.

    Args:
        plugin_dir: Directory to scan for plugin files.
        allowed_plugins: Whitelist of plugin names to load. If provided,
            only plugins whose name (stem or PLUGIN_NAME) is in this set
            will be loaded. Pass ``None`` to load all plugins (legacy
            behaviour). An empty set means load nothing.

    Returns a dict mapping plugin names to agent classes.
    """
    plugins: dict[str, type] = {}
    plugin_path = Path(plugin_dir)
    if not plugin_path.is_dir():
        return plugins

    for py_file in plugin_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        # Whitelist check: skip plugins not in the allowed set.
        stem_name = py_file.stem
        if allowed_plugins is not None and stem_name not in allowed_plugins:
            logger.debug("Skipping plugin %s (not in allowed_plugins whitelist)", py_file)
            continue

        try:
            spec = importlib.util.spec_from_file_location(f"plugin_{py_file.stem}", py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Look for an Agent class or any BaseReviewAgent subclass
            agent_cls = getattr(module, "Agent", None)
            if agent_cls is None:
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and issubclass(attr, BaseReviewAgent)
                            and attr is not BaseReviewAgent):
                        agent_cls = attr
                        break

            if agent_cls is not None:
                name = getattr(agent_cls, "PLUGIN_NAME", py_file.stem)
                # Double-check the resolved name against whitelist.
                if allowed_plugins is not None and name not in allowed_plugins:
                    logger.debug("Skipping plugin %s (name '%s' not in allowed_plugins)", py_file, name)
                    continue
                plugins[name] = agent_cls
                logger.info("Loaded plugin agent: %s from %s", name, py_file)
        except Exception as exc:
            logger.warning("Failed to load plugin %s: %s", py_file, exc)

    return plugins


@dataclass
class ReviewState:
    """State shared across the review workflow.

    Each agent writes to its own field so that LangGraph's shallow-merge
    semantics work correctly with concurrent runners.
    """
    code: str = ""
    file_path: str = ""
    style_result: AgentResult | None = None
    security_result: AgentResult | None = None
    performance_result: AgentResult | None = None
    logic_result: AgentResult | None = None
    repo_result: AgentResult | None = None
    refactor_result: AgentResult | None = None
    fix_result: AgentResult | None = None
    test_result: AgentResult | None = None
    doc_result: AgentResult | None = None
    final_result: AgentResult | None = None
    plugin_results: dict[str, AgentResult] = field(default_factory=dict)


# Reverse-lookup: agent name → ReviewState field name.
_STATE_ATTR_MAP: dict[str, str] = {name: f"{name}_result" for name in _AGENT_CLASSES}


class CodeReviewWorkflow:
    """Orchestrates the multi-agent code review using LangGraph."""

    def __init__(self, model: str | None = None, config: RevHiveConfig | None = None, plugin_dir: str = "plugins",
                 api_key: str | None = None, base_url: str | None = None,
                 enabled_agents: list[str] | None = None,
                 allow_plugins: bool = False,
                 max_concurrent_agents: int | None = None,
                 fallback_models: list[str] | None = None,
                 budget_seconds: float | None = None):
        self.config = config or load_config()
        _api_key = api_key or os.getenv("LLM_API_KEY")
        # A preset name (e.g. --model ollama) implies its endpoint. Falling
        # back to the MiMo default regardless would send a pick like
        # "ollama" to a remote host and demand a key for it.
        from revhive.config import MODEL_PRESETS
        _preset = MODEL_PRESETS.get(model or "", {})
        _base_url = (base_url or os.getenv("LLM_BASE_URL")
                     or _preset.get("base_url")
                     or "https://api.xiaomimimo.com/v1")

        # Alternate models tried when the primary one is rate-limited. Many
        # free/low-tier plans limit *per model*, so rotating models keeps a
        # review moving instead of stalling for minutes.
        self._fallback_models = [m for m in (fallback_models or []) if m]
        self._model_cursor = 0

        # Wall-clock budget. A rate-limited provider can retry for the better
        # part of an hour; a hosted run has to return something before the
        # caller gives up, so agents that cannot start in time are skipped and
        # the report says it is partial. Unset means no limit (CLI usage).
        self._deadline = time.monotonic() + budget_seconds if budget_seconds else None
        self._cooldown_until = 0.0
        if self._deadline:
            logger.info("Review budget: %.0fs", budget_seconds)

        # Concurrency control: config > explicit param > default 3.
        _max_concurrent = max_concurrent_agents or getattr(self.config, "max_concurrent_agents", 3) or 3
        self._semaphore = asyncio.Semaphore(_max_concurrent)
        logger.info("Concurrency limit: %d agents", _max_concurrent)

        # Load built-in + plugin agents (with whitelist if allow_plugins is set).
        all_agents = dict(_AGENT_CLASSES)
        if allow_plugins:
            allowed_names = getattr(self.config, "allowed_plugins", None)
            plugins = load_plugins(plugin_dir, allowed_plugins=allowed_names)
            all_agents.update(plugins)
        _model = model or self.config.model or os.getenv("LLM_MODEL", "mimo-v2.5-pro")

        common_kwargs = {"model": _model, "api_key": _api_key, "base_url": _base_url, "request_timeout": 90}

        # Apply agent filter: explicit list > config > all agents.
        self.agents: dict[str, object] = {}
        for name, cls in all_agents.items():
            if enabled_agents is not None:
                if name in enabled_agents:
                    self.agents[name] = cls(**common_kwargs)
            elif self.config.is_agent_enabled(name):
                self.agents[name] = cls(**common_kwargs)

        self.coordinator = CoordinatorAgent(**common_kwargs)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow with only enabled agents."""
        workflow = StateGraph(ReviewState)

        for name in self.agents:
            workflow.add_node(f"{name}_review", self._make_runner(name))

        review_nodes = [f"{name}_review" for name in self.agents]
        workflow.add_node("coordinate", self._run_coordinate)

        for node in review_nodes:
            workflow.add_edge(START, node)
            workflow.add_edge(node, "coordinate")

        workflow.add_edge("coordinate", END)
        return workflow.compile()

    def _make_runner(self, agent_name: str):
        """Return an async runner that reviews with the named agent."""
        agent = self.agents[agent_name]
        is_plugin = agent_name not in _STATE_ATTR_MAP

        async def _run(state: ReviewState) -> dict:
            result = await self._safe_review(agent, state)
            # Apply severity threshold filtering if configured.
            threshold = self.config.get_severity_threshold(agent_name)
            if threshold is not None and result.findings:
                min_level = SEVERITY_ORDER.get(threshold.value, 99)
                result.findings = [f for f in result.findings if SEVERITY_ORDER.get(f.severity.value, 99) <= min_level]
            if is_plugin:
                plugins = dict(state.plugin_results)
                plugins[agent_name] = result
                return {"plugin_results": plugins}
            return {_STATE_ATTR_MAP[agent_name]: result}

        return _run

    async def _safe_review(self, agent, state: ReviewState) -> AgentResult:
        """Run a single agent review with concurrency control and retry logic.

        Uses an asyncio.Semaphore to limit concurrent LLM calls. Note that
        ``agent.review()`` catches its own LLM exceptions and returns an
        ``AgentResult`` with ``failed=True`` — so we must inspect the result,
        not just catch exceptions, or the retry path would never fire.

        Rate limits get a much larger retry budget than other errors: free and
        low-tier keys are commonly limited to a few requests / minute, and a
        review that takes three minutes is still far better than one that
        reports nothing.
        """
        if self._remaining() is not None and self._remaining() < _MIN_AGENT_SECONDS:
            return AgentResult(
                agent_name=agent.name,
                summary="Skipped — the review time budget ran out before this agent started.",
                failed=True,
                error="budget_exhausted",
            )

        max_transient_retries = 3
        # Nine agents hitting one low-quota key: each retrying on its own
        # backoff keeps the provider throttled forever (measured: a Pro review
        # took nine minutes and still came back partial). Fewer, shorter
        # retries plus a shared cooldown gives the quota room to recover.
        max_rate_limit_retries = 4
        base_delay = 1.0
        rate_limit_delay = 20.0

        transient_attempts = 0
        rate_limit_attempts = 0
        model_switches = 0
        max_model_switches = max(1, len(self._fallback_models)) * 2

        await self._wait_for_cooldown()

        async with self._semaphore:
            # Re-check after the queue: an agent may have waited minutes for
            # a slot while earlier agents burned the budget on retries.
            if self._remaining() is not None and self._remaining() < _MIN_AGENT_SECONDS:
                return AgentResult(
                    agent_name=agent.name,
                    summary="Skipped — the review time budget ran out before this agent started.",
                    failed=True,
                    error="budget_exhausted",
                )
            while True:
                try:
                    result = await agent.review(state.code, state.file_path)
                except Exception as exc:  # defensive: review() should not raise
                    exc_str = str(exc).lower()
                    result = AgentResult(agent_name=agent.name, summary=f"Agent error: {exc}",
                                         failed=True, error=str(exc))
                else:
                    if not result.failed:
                        return result
                    exc_str = (result.error or result.summary).lower()

                is_rate_limit = any(kw in exc_str for kw in ("429", "rpm", "tpm", "quota", "rate limit"))
                is_transient = is_rate_limit or any(kw in exc_str for kw in (
                    "timeout", "503", "502", "500", "connection", "temporary", "overloaded",
                ))

                if is_rate_limit and self._fallback_models and model_switches < max_model_switches:
                    next_model = self._fallback_models[self._model_cursor % len(self._fallback_models)]
                    self._model_cursor += 1
                    current = getattr(agent, "_model", None)
                    if next_model != current and hasattr(agent, "switch_model"):
                        model_switches += 1
                        agent.switch_model(next_model)
                        logger.warning(
                            "%s rate-limited on %s — switched to %s and retrying",
                            agent.name, current, next_model,
                        )
                        await asyncio.sleep(2)
                        continue

                if is_rate_limit and rate_limit_attempts < max_rate_limit_retries:
                    rate_limit_attempts += 1
                    delay = min(rate_limit_delay * rate_limit_attempts, 75.0) + random.uniform(0, 3)
                    # Every agent waits out the same window instead of each
                    # burning its own retries against a provider that is
                    # throttling all of them for the same reason.
                    self._start_cooldown(delay)
                    if self._remaining() is not None and delay > self._remaining() - 10:
                        # Sleeping past the deadline only delays the report; the
                        # customer learns nothing new from the wait.
                        logger.warning(
                            "%s rate-limited with %.0fs left — reporting partial results",
                            agent.name, self._remaining(),
                        )
                        result.error = "rate_limited: provider kept throttling until the review budget ran out"
                        return result
                    logger.warning(
                        "%s rate-limited (attempt %d/%d), retrying in %.1fs",
                        agent.name, rate_limit_attempts, max_rate_limit_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if is_transient and not is_rate_limit and transient_attempts < max_transient_retries - 1:
                    transient_attempts += 1
                    delay = base_delay * (2 ** transient_attempts) + random.uniform(0, 1)
                    logger.warning(
                        "%s transient error (attempt %d/%d), retrying in %.1fs",
                        agent.name, transient_attempts + 1, max_transient_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error("%s failed: %s", agent.name, exc_str[:300])
                return result

    def _start_cooldown(self, seconds: float) -> None:
        """Hold every agent back for ``seconds`` (rate limits are per key)."""
        until = time.monotonic() + seconds
        if until > self._cooldown_until:
            self._cooldown_until = until

    async def _wait_for_cooldown(self) -> None:
        while True:
            remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            # Never sleep past the review budget just to respect a cooldown.
            budget_left = self._remaining()
            if budget_left is not None and remaining > budget_left - 10:
                return
            await asyncio.sleep(remaining)

    def _remaining(self) -> float | None:
        """Seconds left in the review budget, or None when there is no budget."""
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    async def _run_coordinate(self, state: ReviewState) -> dict:
        """Collect results from all enabled agents and synthesize."""
        results = []
        for name in self.agents:
            # Built-in agents have dedicated state fields
            if name in _STATE_ATTR_MAP:
                r = getattr(state, _STATE_ATTR_MAP[name], None)
            else:
                # Plugin agents use the plugin_results dict
                r = state.plugin_results.get(name)
            if r is not None:
                results.append(r)
        final = await self.coordinator.synthesize(results)

        # If no agent actually completed, the synthesized "0 findings / low
        # risk" report is a lie — surface it as a failure instead, so callers
        # never publish a clean bill of health for unreviewed code.
        if results and all(r.failed for r in results):
            final.failed = True
            final.error = next((r.error or r.summary for r in results if r.error or r.summary), "all agents failed")
            final.summary = (
                f"⚠️ Review could not run — all {len(results)} agents failed. "
                f"Last error: {final.error}"
            )
        elif any(r.failed for r in results):
            # Partial coverage: be explicit about what was NOT checked.
            failed_names = ", ".join(r.agent_name for r in results if r.failed)
            ok_count = sum(1 for r in results if not r.failed)
            final.summary += (
                f"\n\n⚠️ **Partial review** — {ok_count}/{len(results)} agents completed. "
                f"Not checked: {failed_names}."
            )
        return {"final_result": final}

    async def run(self, code: str, file_path: str = "", pr_description: str | None = None,
                  passes: int = 1) -> AgentResult:
        """Run the full review workflow.

        *pr_description* simulates the description a reviewer would see on a
        real pull request. It is prepended to the reviewed text and clearly
        delimited, so agents can weigh it as context — the way a human
        reviewer reads the PR description alongside the diff.

        *passes* runs the whole review N times independently. Single-pass
        severity is noisy: the same model on the same diff produced
        risk-weighted scores of 45 vs 54 across two identical passes.
        With N > 1 the reported result comes from the median-risk pass
        (deterministic, no cross-pass finding matching — the same defect
        is re-described in unrelated words across passes, so lexical
        matching is not trustworthy), and the summary discloses every
        pass's risk level so the reader sees the disagreement.
        """
        if pr_description:
            code = (
                "--- PR description (developer-provided context) ---\n"
                f"{pr_description.strip()}\n"
                "--- end PR description ---\n\n"
                + code
            )
        passes = max(1, int(passes))
        finals = []
        for _ in range(passes):
            final_state = await self.graph.ainvoke(ReviewState(code=code, file_path=file_path))
            finals.append(final_state.get("final_result",
                                          AgentResult(agent_name="System", summary="No results")))
        if passes == 1:
            return finals[0]
        return self._merge_passes(finals)

    @staticmethod
    def _merge_passes(finals: list[AgentResult]) -> AgentResult:
        """Pick the median-risk pass as the report and disclose the rest.

        Failed passes are excluded from the median: a failed pass reports
        no findings and would fake a low risk score. If every pass failed
        the first one is returned so the caller sees the error.
        """
        ok = [f for f in finals if not f.failed]
        if not ok:
            return finals[0]
        scored = sorted(ok, key=lambda f: (f.risk_score if f.risk_score is not None else 0))
        median = scored[len(scored) // 2 if len(scored) % 2 == 1 else len(scored) // 2 - 1]
        # For an even count take the quieter middle pass: severity describes
        # what the diff proves, and a defect one pass could not substantiate
        # should not inherit the other pass's alarm.
        median.risk_score = median.risk_score or 0
        lines = ["",
                 f"🛡️ Consensus of {len(finals)} independent passes — "
                 f"risk scores: " + ", ".join(
                     f"pass {i + 1}: {f.risk_score if f.risk_score is not None else 'failed'}"
                     for i, f in enumerate(finals))
                 + f". Report shows the median pass (score {median.risk_score}/100)."]
        if len(ok) < len(finals):
            lines.append(f"  {len(finals) - len(ok)} pass(es) failed and were excluded from the median.")
        median.summary += "\n".join(lines)
        return median

    @staticmethod
    def _validate_diff_ref(diff_ref: str) -> None:
        """Validate that diff_ref contains only safe characters for git.

        Allowed patterns:
        - ``HEAD~N`` (e.g. HEAD~1, HEAD~3)
        - Full 40-character hex commit SHA
        - Branch name (alphanumeric, underscores, hyphens, slashes)

        Raises ``ValueError`` if the reference doesn't match.
        """
        pattern = r'^(HEAD~\d+|[a-f0-9]{40}|[a-zA-Z0-9_/-]+)$'
        if not re.match(pattern, diff_ref):
            raise ValueError(f"Invalid diff reference: {diff_ref}")

    async def run_from_diff(self, diff_ref: str, pr_description: str | None = None,
                            passes: int = 1) -> AgentResult:
        """Run review on a git diff."""
        import asyncio as _asyncio
        self._validate_diff_ref(diff_ref)
        proc = await _asyncio.create_subprocess_exec(
            "git", "diff", diff_ref,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git diff failed: {stderr.decode()}")
        return await self.run(code=stdout.decode(), file_path=f"diff:{diff_ref}",
                              pr_description=pr_description, passes=passes)

    async def run_on_repo(self, repo_path: str, file_patterns: list[str] = None) -> list[AgentResult]:
        """Run review on an entire repository. High token consumption scenario.

        Scans all matching files, runs 9 agents per file, and produces
        a consolidated repository-level report.
        """
        if file_patterns is None:
            file_patterns = [
                "**/*.py", "**/*.js", "**/*.jsx", "**/*.mjs",
                "**/*.ts", "**/*.tsx", "**/*.go", "**/*.rs",
                "**/*.java", "**/*.c", "**/*.cpp", "**/*.h",
                "**/*.hpp", "**/*.rb", "**/*.php", "**/*.swift", "**/*.kt",
            ]

        files = []
        for pattern in file_patterns:
            files.extend(glob.glob(str(Path(repo_path) / pattern), recursive=True))

        # Apply ignore patterns from config.
        files = [f for f in files if not self.config.should_ignore(f)]

        _MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB
        results = []
        for f in files[:50]:
            try:
                if Path(f).stat().st_size > _MAX_FILE_SIZE:
                    logger.info("Skipping %s (exceeds 1 MB limit)", f)
                    continue
                code = Path(f).read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("Skipping %s: %s", f, exc)
                continue
            result = await self.run(code=code, file_path=f)
            results.append(result)

        return results

    async def run_ci_mode(self, diff_ref: str = "HEAD~1") -> AgentResult:
        """CI/CD integration mode — runs full review on every PR.

        Designed for automated pipeline: git hook / GitHub Action / GitLab CI.
        Runs all 9 agents + coordinator + repo-level scan.
        """
        diff_result = await self.run_from_diff(diff_ref)

        import asyncio as _asyncio
        self._validate_diff_ref(diff_ref)
        proc = await _asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", diff_ref,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
        )
        stdout, _ = await proc.communicate()

        _MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB
        full_results = [diff_result]
        for file in stdout.decode().strip().split("\n"):
            if file and os.path.exists(file):
                try:
                    if Path(file).stat().st_size > _MAX_FILE_SIZE:
                        logger.info("Skipping %s (exceeds 1 MB limit)", file)
                        continue
                    code = Path(file).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    logger.warning("Skipping %s: %s", file, exc)
                    continue
                result = await self.run(code=code, file_path=file)
                full_results.append(result)

        final = await self.coordinator.synthesize(full_results)
        return final


@dataclass
class ReviewReport:
    """Formatted review report."""
    result: AgentResult

    def to_markdown(self) -> str:
        """Render the review result as a concise Markdown report.

        Critical/High findings are shown in full. Medium are listed by title.
        Low are summarized as a count. Full details are in a collapsed section.
        """
        # A failed run must never be rendered as a clean report.
        if getattr(self.result, "failed", False):
            return (
                "# RevHive Review Report\n\n"
                "## ⚠️ Review could not run\n\n"
                f"{self.result.summary}\n\n"
                "**No code was reviewed.** Fix the error above (usually an "
                "invalid key, exhausted quota, or rate limit) and re-run.\n"
            )

        lines = [
            "# RevHive Review Report\n",
            "## 📊 Overview\n",
            self.result.summary,
            f"\n**Total Findings:** {len(self.result.findings)}\n",
        ]

        critical_high = [f for f in self.result.findings if f.severity.value in ("critical", "high")]
        medium = [f for f in self.result.findings if f.severity.value == "medium"]
        low = [f for f in self.result.findings if f.severity.value == "low"]

        # Critical & High — full detail
        if critical_high:
            lines.append("\n## ⚠️ Critical & High Findings\n")
            for f in critical_high:
                emoji = "🔴" if f.severity.value == "critical" else "🟠"
                lines.append(f"### {emoji} [{f.severity.value.upper()}] {f.title}")
                if f.line_number is not None:
                    lines.append(f"**Line:** {f.line_number}")
                lines.append(f"\n{f.description}")
                if f.suggestion:
                    lines.append(f"\n**Suggestion:** {f.suggestion}")
                lines.append("")

        # Medium — title only
        if medium:
            lines.append(f"\n## 🟡 Medium ({len(medium)})\n")
            for f in medium:
                loc = f" (`{f.file_path}:{f.line_number}`)" if f.file_path and f.line_number else ""
                lines.append(f"- {f.title}{loc}")

        # Low — count only
        if low:
            lines.append(f"\n## 🟢 Low ({len(low)})\n")
            lines.append(
                f"{len(low)} low-severity issue{'s' if len(low) != 1 else ''} found. "
                "Expand below for details.\n"
            )

        # Full details in collapsed section
        all_findings = self.result.findings
        if all_findings:
            lines.append("\n<details><summary>📋 Full Findings Detail</summary>\n")
            categories: dict[str, list] = {}
            for f in all_findings:
                categories.setdefault(f.agent, []).append(f)
            for agent, findings in categories.items():
                count = len(findings)
                lines.append(f"\n### {agent} ({count} issue{'s' if count != 1 else ''})\n")
                for f in findings:
                    emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(f.severity.value, "⚪")
                    lines.append(f"#### {emoji} [{f.severity.value.upper()}] {f.title}")
                    if f.line_number is not None:
                        lines.append(f"**Line:** {f.line_number}")
                    lines.append(f"\n{f.description}")
                    if f.suggestion:
                        lines.append(f"\n**Suggestion:** {f.suggestion}")
                    lines.append("")
            lines.append("</details>")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Render the review result as a JSON string."""
        data = {
            "failed": bool(getattr(self.result, "failed", False)),
            "error": getattr(self.result, "error", ""),
            "summary": self.result.summary,
            "total_findings": len(self.result.findings),
            "findings": [
                {
                    "agent": f.agent,
                    "severity": f.severity.value,
                    "title": f.title,
                    "line": f.line_number,
                    "description": f.description,
                    "suggestion": f.suggestion,
                }
                for f in self.result.findings
            ],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)
