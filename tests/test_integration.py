"""Integration tests for the full review workflow.

Tests the end-to-end flow: code → agents → coordinator → report.
Uses mock LLM to avoid API calls.
"""

import pytest
from unittest.mock import AsyncMock

from revhive.agents.base import AgentResult
from revhive.demo import DemoReviewWorkflow
from revhive.graph.workflow import CodeReviewWorkflow, ReviewReport


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Mock LLM that returns realistic findings
# ---------------------------------------------------------------------------


class _MockLLM:
    """Fake ChatOpenAI that returns security-focused findings."""

    model_name = "mock-model"
    temperature = 0.1
    max_retries = 1
    request_timeout = 10

    async def ainvoke(self, messages):
        class FakeResponse:
            content = """- Severity: CRITICAL
- Title: SQL Injection via f-string
- Line: 5
- Description: User input interpolated into SQL query allows database compromise.
- Suggestion: Use parameterized queries with placeholders.

- Severity: HIGH
- Title: Hardcoded API Secret
- Line: 3
- Description: Production credentials committed in source code.
- Suggestion: Load from environment variables.

Summary: 2 critical/high severity issues found."""

            class response_metadata:
                token_usage = {"total_tokens": 1500}

        return FakeResponse()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestDemoWorkflowIntegration:
    """Test the demo workflow end-to-end."""

    def test_demo_produces_findings(self):
        """Demo mode should produce findings without any API key."""
        demo = DemoReviewWorkflow()
        result = demo.run(code="x = 1", file_path="test.py")

        assert result.agent_name == "CoordinatorAgent"
        assert len(result.findings) > 0
        assert result.risk_score is not None
        assert result.risk_score > 0

    def test_demo_report_markdown(self):
        """Demo report should be valid markdown with key sections."""
        demo = DemoReviewWorkflow()
        result = demo.run(code="x = 1", file_path="test.py")
        report = ReviewReport(result).to_markdown()

        assert "RevHive Review Report" in report
        assert "Risk Score" in report
        assert "Critical" in report or "HIGH" in report or "MEDIUM" in report

    def test_demo_report_json(self):
        """Demo report should be valid JSON."""
        import json
        demo = DemoReviewWorkflow()
        result = demo.run(code="x = 1", file_path="test.py")
        report_json = ReviewReport(result).to_json()

        data = json.loads(report_json)
        assert "summary" in data
        assert "findings" in data
        assert "total_findings" in data
        assert data["total_findings"] > 0

    def test_demo_findings_have_file_path(self):
        """Demo findings should include file_path for inline comments."""
        demo = DemoReviewWorkflow()
        result = demo.run(code="x = 1", file_path="app.py")

        findings_with_path = [f for f in result.findings if f.file_path]
        assert len(findings_with_path) > 0
        assert all(f.file_path == "app.py" for f in findings_with_path)

    def test_demo_risk_score_range(self):
        """Risk score should be between 0 and 100."""
        demo = DemoReviewWorkflow()
        result = demo.run(code="x = 1", file_path="test.py")

        assert 0 <= result.risk_score <= 100

    def test_demo_severity_ordering(self):
        """Findings should be sorted by severity (critical first)."""
        demo = DemoReviewWorkflow()
        result = demo.run(code="x = 1", file_path="test.py")

        severities = [f.severity.value for f in result.findings]
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        ordered = [severity_order.get(s, 99) for s in severities]
        assert ordered == sorted(ordered)


class TestWorkflowWithMockLLM:
    """Test the real workflow with mocked LLM."""

    @pytest.mark.asyncio
    async def test_workflow_runs_with_mock(self):
        """Full workflow should complete with mock LLM."""
        import os
        os.environ.setdefault("LLM_API_KEY", "test-key")

        workflow = CodeReviewWorkflow(model="test-model")
        # Replace all agent LLMs with mock
        for agent in workflow.agents.values():
            agent.llm = _MockLLM()
        workflow.coordinator.llm = _MockLLM()

        result = await workflow.run(code="x = 1", file_path="test.py")

        assert result is not None
        assert isinstance(result, AgentResult)
        assert result.agent_name == "CoordinatorAgent"

    @pytest.mark.asyncio
    async def test_workflow_produces_report(self):
        """Workflow output should be convertible to markdown report."""
        import os
        os.environ.setdefault("LLM_API_KEY", "test-key")

        workflow = CodeReviewWorkflow(model="test-model")
        for agent in workflow.agents.values():
            agent.llm = _MockLLM()
        workflow.coordinator.llm = _MockLLM()

        result = await workflow.run(code="x = 1", file_path="test.py")
        report = ReviewReport(result).to_markdown()

        assert "RevHive Review Report" in report
        assert "Risk Score" in report

    @pytest.mark.asyncio
    async def test_workflow_handles_agent_failure(self):
        """Workflow should not crash if one agent fails."""
        import os
        os.environ.setdefault("LLM_API_KEY", "test-key")

        workflow = CodeReviewWorkflow(model="test-model")
        for name, agent in workflow.agents.items():
            if name == "security":
                # Make security agent fail
                agent.llm = AsyncMock()
                agent.llm.ainvoke.side_effect = Exception("API error")
            else:
                agent.llm = _MockLLM()
        workflow.coordinator.llm = _MockLLM()

        result = await workflow.run(code="x = 1", file_path="test.py")

        # Should still produce a result despite one agent failing
        assert result is not None
        assert result.agent_name == "CoordinatorAgent"
