"""LangGraph workflow for multi-agent code review.

Default backend: MiMo (https://api.xiaomimimo.com/v1).
"""

import json
import logging
import os
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from langgraph.graph import StateGraph, END, START

from codeguardian.agents import (
    StyleAgent,
    SecurityAgent,
    PerformanceAgent,
    LogicAgent,
    RepoAgent,
    RefactorAgent,
    FixAgent,
    TestAgent,
    DocAgent,
    CoordinatorAgent,
)
from codeguardian.agents.base import AgentResult
from codeguardian.config import GuardianConfig, load_config

logger = logging.getLogger(__name__)


@dataclass
class ReviewState:
    """State shared across the review workflow."""
    code: str = ""
    file_path: str = ""
    style_result: Optional[AgentResult] = None
    security_result: Optional[AgentResult] = None
    performance_result: Optional[AgentResult] = None
    logic_result: Optional[AgentResult] = None
    repo_result: Optional[AgentResult] = None
    refactor_result: Optional[AgentResult] = None
    fix_result: Optional[AgentResult] = None
    test_result: Optional[AgentResult] = None
    doc_result: Optional[AgentResult] = None
    final_result: Optional[AgentResult] = None


class CodeReviewWorkflow:
    """Orchestrates the multi-agent code review using LangGraph."""

    # Mapping from YAML agent names to (agent class, state attribute).
    _AGENT_REGISTRY: dict[str, tuple[type, str]] = {
        "style":       (StyleAgent,       "style_result"),
        "security":    (SecurityAgent,    "security_result"),
        "performance": (PerformanceAgent, "performance_result"),
        "logic":       (LogicAgent,       "logic_result"),
        "repo":        (RepoAgent,        "repo_result"),
        "refactor":    (RefactorAgent,    "refactor_result"),
        "fix":         (FixAgent,         "fix_result"),
        "test":        (TestAgent,        "test_result"),
        "doc":         (DocAgent,         "doc_result"),
    }

    def __init__(self, model: Optional[str] = None, config: Optional[GuardianConfig] = None):
        self.config = config or load_config()
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL", "https://api.xiaomimimo.com/v1")
        model = model or self.config.model or os.getenv("LLM_MODEL", "mimo-v2.5-pro")

        common_kwargs = {"model": model, "api_key": api_key, "base_url": base_url, "request_timeout": 120}

        # Only instantiate agents that are enabled in the config.
        self.agents: dict[str, object] = {}
        for name, (cls, _state_attr) in self._AGENT_REGISTRY.items():
            if self.config.is_agent_enabled(name):
                self.agents[name] = cls(**common_kwargs)
                setattr(self, f"{name}_agent", self.agents[name])

        self.coordinator = CoordinatorAgent(**common_kwargs)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow with only enabled agents."""
        workflow = StateGraph(ReviewState)

        # Map agent name -> runner method.  Each runner returns a dict
        # keyed by the ReviewState attribute for that agent.
        _runners: dict[str, callable] = {}
        for name in self.agents:
            _runners[name] = self._make_runner(name)

        for name, runner in _runners.items():
            workflow.add_node(f"{name}_review", runner)

        review_nodes = [f"{name}_review" for name in _runners]

        workflow.add_node("coordinate", self._run_coordinate)

        for node in review_nodes:
            workflow.add_edge(START, node)

        for node in review_nodes:
            workflow.add_edge(node, "coordinate")

        workflow.add_edge("coordinate", END)

        return workflow.compile()

    def _make_runner(self, agent_name: str):
        """Return an async runner that reviews with the named agent."""
        agent = self.agents[agent_name]
        _, state_attr = self._AGENT_REGISTRY[agent_name]

        async def _run(state: ReviewState) -> dict:
            result = await self._safe_review(agent, state)
            # Apply severity threshold filtering if configured.
            threshold = self.config.get_severity_threshold(agent_name)
            if threshold is not None and result.findings:
                from codeguardian.agents.base import Severity as _Sev
                _order = {s: i for i, s in enumerate(_Sev)}
                min_level = _order[threshold]
                result.findings = [f for f in result.findings if _order.get(f.severity, 99) >= min_level]
            return {state_attr: result}

        return _run

    async def _safe_review(self, agent, state: ReviewState) -> AgentResult:
        """Run a single agent review, returning a failure result on exception
        rather than crashing the entire workflow."""
        try:
            return await agent.review(state.code, state.file_path)
        except Exception as exc:
            logger.exception("%s failed: %s", agent.name, exc)
            return AgentResult(
                agent_name=agent.name,
                summary=f"Agent error: {exc}",
            )

    async def _run_coordinate(self, state: ReviewState) -> dict:
        """Collect results from all enabled agents and synthesize."""
        results = []
        for _name, (_cls, state_attr) in self._AGENT_REGISTRY.items():
            if self.config.is_agent_enabled(_name):
                r = getattr(state, state_attr, None)
                if r is not None:
                    results.append(r)
        final = await self.coordinator.synthesize(results)
        return {"final_result": final}

    async def run(self, code: str, file_path: str = "") -> AgentResult:
        """Run the full review workflow."""
        initial_state = ReviewState(code=code, file_path=file_path)
        final_state = await self.graph.ainvoke(initial_state)
        return final_state.get("final_result", AgentResult(agent_name="System", summary="No results"))

    async def run_from_diff(self, diff_ref: str) -> AgentResult:
        """Run review on a git diff."""
        import subprocess
        result = subprocess.run(
            ["git", "diff", diff_ref],
            capture_output=True, text=True, cwd=os.getcwd()
        )
        if result.returncode != 0:
            raise RuntimeError(f"git diff failed: {result.stderr}")
        return await self.run(code=result.stdout, file_path=f"diff:{diff_ref}")

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

        results = []
        for f in files[:50]:
            code = Path(f).read_text(encoding="utf-8", errors="ignore")
            result = await self.run(code=code, file_path=f)
            results.append(result)

        return results

    async def run_ci_mode(self, diff_ref: str = "HEAD~1") -> AgentResult:
        """CI/CD integration mode — runs full review on every PR.

        Designed for automated pipeline: git hook / GitHub Action / GitLab CI.
        Runs all 9 agents + coordinator + repo-level scan.
        """
        diff_result = await self.run_from_diff(diff_ref)

        import subprocess
        changed = subprocess.run(
            ["git", "diff", "--name-only", diff_ref],
            capture_output=True, text=True, cwd=os.getcwd()
        )

        full_results = [diff_result]
        for file in changed.stdout.strip().split("\n"):
            if file and os.path.exists(file):
                code = Path(file).read_text(encoding="utf-8")
                result = await self.run(code=code, file_path=file)
                full_results.append(result)

        final = await self.coordinator.synthesize(full_results)
        return final


@dataclass
class ReviewReport:
    """Formatted review report."""
    result: AgentResult

    def to_markdown(self) -> str:
        """Render the review result as a Markdown report."""
        lines = [
            "# CodeGuardian Review Report\n",
            "## 📊 Overview\n",
            self.result.summary,
            f"\n**Total Findings:** {len(self.result.findings)}\n",
        ]

        categories = {}
        for f in self.result.findings:
            categories.setdefault(f.agent, []).append(f)

        for agent, findings in categories.items():
            lines.append(f"\n## {agent} ({len(findings)} issues)\n")
            for f in findings:
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(f.severity.value, "⚪")
                lines.append(f"### {emoji} [{f.severity.value.upper()}] {f.title}")
                if f.line_number is not None:
                    lines.append(f"**Line:** {f.line_number}")
                lines.append(f"\n{f.description}")
                if f.suggestion:
                    lines.append(f"\n**Suggestion:** {f.suggestion}")
                lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Render the review result as a JSON string."""
        data = {
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
