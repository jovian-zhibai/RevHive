"""LangGraph workflow for multi-agent code review.

Default backend: MiMo (https://platform.xiaomimimo.com/api/v1).
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
    CoordinatorAgent,
)
from codeguardian.agents.base import AgentResult

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
    final_result: Optional[AgentResult] = None


class CodeReviewWorkflow:
    """Orchestrates the multi-agent code review using LangGraph."""

    def __init__(self, model: Optional[str] = None):
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL", "https://platform.xiaomimimo.com/api/v1")
        model = model or os.getenv("LLM_MODEL", "mimo-v2.5-pro")

        common_kwargs = {"model": model, "api_key": api_key, "base_url": base_url}

        self.style_agent = StyleAgent(**common_kwargs)
        self.security_agent = SecurityAgent(**common_kwargs)
        self.performance_agent = PerformanceAgent(**common_kwargs)
        self.logic_agent = LogicAgent(**common_kwargs)
        self.repo_agent = RepoAgent(**common_kwargs)
        self.refactor_agent = RefactorAgent(**common_kwargs)
        self.coordinator = CoordinatorAgent(**common_kwargs)

        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow."""
        workflow = StateGraph(ReviewState)

        workflow.add_node("style_review", self._run_style)
        workflow.add_node("security_review", self._run_security)
        workflow.add_node("performance_review", self._run_performance)
        workflow.add_node("logic_review", self._run_logic)
        workflow.add_node("repo_review", self._run_repo)
        workflow.add_node("refactor_review", self._run_refactor)
        workflow.add_node("coordinate", self._run_coordinate)

        review_nodes = [
            "style_review",
            "security_review",
            "performance_review",
            "logic_review",
            "repo_review",
            "refactor_review",
        ]

        for node in review_nodes:
            workflow.add_edge(START, node)

        for node in review_nodes:
            workflow.add_edge(node, "coordinate")

        workflow.add_edge("coordinate", END)

        return workflow.compile()

    async def _run_style(self, state: ReviewState) -> dict:
        result = await self._safe_review(self.style_agent, state)
        return {"style_result": result}

    async def _run_security(self, state: ReviewState) -> dict:
        result = await self._safe_review(self.security_agent, state)
        return {"security_result": result}

    async def _run_performance(self, state: ReviewState) -> dict:
        result = await self._safe_review(self.performance_agent, state)
        return {"performance_result": result}

    async def _run_logic(self, state: ReviewState) -> dict:
        result = await self._safe_review(self.logic_agent, state)
        return {"logic_result": result}

    async def _run_repo(self, state: ReviewState) -> dict:
        result = await self._safe_review(self.repo_agent, state)
        return {"repo_result": result}

    async def _run_refactor(self, state: ReviewState) -> dict:
        result = await self._safe_review(self.refactor_agent, state)
        return {"refactor_result": result}

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
        results = [
            r for r in [
                state.style_result,
                state.security_result,
                state.performance_result,
                state.logic_result,
                state.repo_result,
                state.refactor_result,
            ]
            if r is not None
        ]
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

        Scans all matching files, runs 6 agents per file, and produces
        a consolidated repository-level report.
        """
        if file_patterns is None:
            file_patterns = ["**/*.py", "**/*.js", "**/*.ts"]

        files = []
        for pattern in file_patterns:
            files.extend(glob.glob(str(Path(repo_path) / pattern), recursive=True))

        results = []
        for f in files[:50]:
            code = Path(f).read_text(encoding="utf-8", errors="ignore")
            result = await self.run(code=code, file_path=f)
            results.append(result)

        return results

    async def run_ci_mode(self, diff_ref: str = "HEAD~1") -> AgentResult:
        """CI/CD integration mode — runs full review on every PR.

        Designed for automated pipeline: git hook / GitHub Action / GitLab CI.
        Runs all 6 agents + coordinator + repo-level scan.
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
                if f.line_number:
                    lines.append(f"**Line:** {f.line_number}")
                lines.append(f"\n{f.description}")
                if f.suggestion:
                    lines.append(f"\n**Suggestion:** {f.suggestion}")
                lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
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
