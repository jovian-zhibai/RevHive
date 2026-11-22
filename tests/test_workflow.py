"""Tests for the LangGraph review workflow."""

from pathlib import Path

import pytest

from revhive.agents.base import AgentResult, ReviewFinding, Severity
from revhive.graph.workflow import ReviewReport, ReviewState, build_provenance


def test_review_state_defaults():
    state = ReviewState(code="print('hello')", file_path="test.py")
    assert state.code == "print('hello')"
    assert state.file_path == "test.py"
    assert state.style_result is None
    assert state.security_result is None


def test_review_state_with_results():
    state = ReviewState(
        code="x = 1",
        file_path="a.py",
        style_result=AgentResult(agent_name="StyleAgent", findings=[]),
    )
    assert state.style_result is not None
    assert state.security_result is None


# ---------------------------------------------------------------------------
# ReviewReport tests
# ---------------------------------------------------------------------------


def test_report_markdown_empty():
    result = AgentResult(
        agent_name="Coordinator",
        findings=[],
        summary="No issues found.",
    )
    report = ReviewReport(result)
    md = report.to_markdown()
    assert "RevHive Review Report" in md
    assert "Total Findings:** 0" in md


def test_report_markdown_with_findings():
    findings = [
        ReviewFinding(
            agent="SecurityAgent",
            severity=Severity.HIGH,
            title="SQL Injection",
            description="User input interpolated into SQL query.",
            line_number=12,
            suggestion="Use parameterized queries.",
        ),
        ReviewFinding(
            agent="StyleAgent",
            severity=Severity.LOW,
            title="Missing docstring",
            description="No docstring on public function.",
            line_number=42,
            suggestion="Add docstring.",
        ),
    ]
    result = AgentResult(
        agent_name="Coordinator",
        findings=findings,
        summary="2 issues found.",
    )
    report = ReviewReport(result)
    md = report.to_markdown()

    assert "SQL Injection" in md
    assert "Missing docstring" in md
    assert "[HIGH]" in md
    assert "[LOW]" in md
    assert "Line:** 12" in md
    assert "parameterized queries" in md


def test_report_json():
    findings = [
        ReviewFinding(
            agent="StyleAgent",
            severity=Severity.LOW,
            title="Long line",
            description="Line too long.",
            line_number=99,
            suggestion="Break line.",
        ),
    ]
    result = AgentResult(agent_name="Coordinator", findings=findings, summary="Done.")
    report = ReviewReport(result)
    data = report.to_json()

    import json
    parsed = json.loads(data)
    assert parsed["total_findings"] == 1
    assert parsed["findings"][0]["agent"] == "StyleAgent"
    assert parsed["findings"][0]["severity"] == "low"


def test_report_records_provenance_of_each_agent():
    """A report must say which model ran and how its output was parsed.

    A rate-limit switch or a regex downgrade is a different measurement, so
    the report marks it instead of presenting it as an ordinary result.
    """
    import json

    prov = build_provenance(
        "deepseek-v4-flash",
        {
            "SecurityAgent": {"model": "deepseek-v4-flash", "parse_mode": "structured"},
            "LogicAgent": {"model": "sensenova-6.8-flash-lite", "parse_mode": "regex"},
        },
        [{"agent": "LogicAgent", "from": "deepseek-v4-flash", "to": "sensenova-6.8-flash-lite"}],
    )
    report = ReviewReport(AgentResult(agent_name="Coordinator", summary="Done.", provenance=prov))

    assert prov["regex_fallbacks"] == ["LogicAgent"]
    assert prov["off_model_agents"] == ["LogicAgent"]
    assert prov["clean"] is False

    parsed = json.loads(report.to_json())
    assert parsed["provenance"]["clean"] is False
    assert parsed["provenance"]["agents"]["SecurityAgent"]["parse_mode"] == "structured"


def test_provenance_clean_when_every_agent_matched():
    prov = build_provenance(
        "agnes-3.0-flash",
        {"StyleAgent": {"model": "agnes-3.0-flash", "parse_mode": "structured"}},
        [],
    )
    assert prov["clean"] is True


def test_verify_run_flags_degraded_cells(tmp_path):
    """The benchmark gate must not read a switched model as a clean cell."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_run", Path(__file__).resolve().parents[1] / "scripts" / "verify_run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    clean_log = tmp_path / "c01-agnes.log"
    clean_log.write_text("Report saved to /tmp/out/c01-agnes.json\n")
    assert module.classify(clean_log)["verdict"] == module.CLEAN

    switched_log = tmp_path / "c02-ds.log"
    switched_log.write_text(
        "SecurityAgent rate-limited on sensenova-ds — switched to sensenova-6.8-flash and retrying\n"
        "LogicAgent: structured output failed for diff:HEAD~1, falling back to regex: Request timed out.\n"
    )
    info = module.classify(switched_log)
    assert info["verdict"] == module.DEGRADED
    assert info["model_switches"] == [{"from": "sensenova-ds", "to": "sensenova-6.8-flash"}]
    assert info["regex_fallbacks"] == 1


# ---------------------------------------------------------------------------
# Workflow structure tests (no LLM calls)
# ---------------------------------------------------------------------------


def test_workflow_graph_structure():
    """Verify the LangGraph workflow can be built without errors."""
    import os

    from revhive.config import RevHiveConfig
    from revhive.graph.workflow import CodeReviewWorkflow

    # Use empty config (all agents enabled) to verify full graph.
    cfg = RevHiveConfig()
    os.environ.setdefault("LLM_API_KEY", "test-key")
    workflow = CodeReviewWorkflow(model="mimo-v2.5-pro", config=cfg)
    graph = workflow.graph

    # The graph should be compiled
    assert graph is not None
    # Should have all review nodes + coordinator
    nodes = graph.get_graph().nodes
    assert len(nodes) >= 12  # 9 reviewers + coordinator + __start__ + __end__


# ---------------------------------------------------------------------------
# Run workflow with demo/mocked LLM
# ---------------------------------------------------------------------------


class _MockLLM:
    """Fake ChatOpenAI that returns a canned response."""

    model_name = "mock-model"
    temperature = 0.1

    async def ainvoke(self, messages):
        class FakeResponse:
            content = """- Severity: MEDIUM
- Title: Mock Finding
- Line: 1
- Description: This is a mock finding from the test harness.
- Suggestion: Fix it.

Summary: All good."""
            response_metadata = {"token_usage": {"total_tokens": 42}}

        return FakeResponse()


@pytest.mark.asyncio
async def test_single_agent_review_with_mock():
    """Test that a single agent can run with a mocked LLM."""
    from revhive.agents.security_agent import SecurityAgent

    agent = SecurityAgent(model="mock", api_key="test-key")
    agent.llm = _MockLLM()

    result = await agent.review("x = 1", "test.py")
    assert result.agent_name == "SecurityAgent"
    assert len(result.findings) == 1
    assert result.findings[0].title == "Mock Finding"
    assert result.findings[0].severity == Severity.MEDIUM


@pytest.mark.asyncio
async def test_coordinator_with_mock_llm():
    """Test coordinator synthesize (doesn't call LLM itself)."""
    from revhive.agents.coordinator import CoordinatorAgent

    coordinator = CoordinatorAgent(model="mock", api_key="test-key")
    results = [
        AgentResult(
            agent_name="A",
            findings=[
                ReviewFinding(
                    agent="A",
                    severity=Severity.HIGH,
                    title="Issue 1",
                    description="desc",
                ),
            ],
            token_usage=100,
        ),
    ]
    result = await coordinator.synthesize(results)
    assert result.agent_name == "CoordinatorAgent"
    assert len(result.findings) == 1


# ---------------------------------------------------------------------------
# Review budget — a hosted run must return *something* before the caller gives up
# ---------------------------------------------------------------------------


class _SlowAgent:
    """Stands in for a real agent; records whether it was asked to review."""

    name = "SlowAgent"

    def __init__(self):
        self.calls = 0

    async def review(self, code, file_path):
        self.calls += 1
        return AgentResult(agent_name=self.name, summary="done")


@pytest.mark.asyncio
async def test_agent_is_skipped_when_the_budget_is_already_gone():
    from revhive.graph.workflow import CodeReviewWorkflow

    workflow = CodeReviewWorkflow.__new__(CodeReviewWorkflow)
    workflow._deadline = 0.0  # long past
    workflow._cooldown_until = 0.0
    workflow.agents = {}

    agent = _SlowAgent()
    result = await workflow._safe_review(agent, ReviewState(code="x = 1", file_path="a.py"))

    assert result.failed is True
    assert result.error == "budget_exhausted"
    assert agent.calls == 0, "a skipped agent must not spend a request"


@pytest.mark.asyncio
async def test_agent_runs_when_there_is_no_budget(monkeypatch):
    import asyncio

    from revhive.graph.workflow import CodeReviewWorkflow

    workflow = CodeReviewWorkflow.__new__(CodeReviewWorkflow)
    workflow._deadline = None
    workflow._cooldown_until = 0.0
    workflow.agents = {}
    workflow._semaphore = asyncio.Semaphore(1)
    workflow._fallback_models = []

    agent = _SlowAgent()
    result = await workflow._safe_review(agent, ReviewState(code="x = 1", file_path="a.py"))

    assert result.failed is False
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_rate_limit_puts_every_agent_on_one_cooldown():
    """Nine agents each retrying alone keep the provider throttled for minutes.

    Only the bookkeeping is asserted here; the sleeps are stubbed so the test
    does not take the four minutes the real backoff would.
    """
    import asyncio
    import time
    from unittest.mock import AsyncMock, patch

    from revhive.graph.workflow import CodeReviewWorkflow

    workflow = CodeReviewWorkflow.__new__(CodeReviewWorkflow)
    workflow._deadline = None
    workflow._cooldown_until = 0.0
    workflow.agents = {}
    workflow._semaphore = asyncio.Semaphore(1)
    workflow._fallback_models = []

    waits = []

    async def recording_wait():
        waits.append(workflow._cooldown_until)

    class ThrottledAgent:
        name = "ThrottledAgent"

        def __init__(self):
            self.calls = 0

        async def review(self, code, file_path):
            self.calls += 1
            return AgentResult(
                agent_name=self.name,
                summary="429 rate limit exceeded",
                failed=True,
                error="429 rate limit exceeded",
            )

    agent = ThrottledAgent()
    started = time.monotonic()
    with patch.object(workflow, "_wait_for_cooldown", recording_wait), \
         patch("revhive.graph.workflow.asyncio.sleep", AsyncMock()):
        result = await workflow._safe_review(agent, ReviewState(code="x = 1", file_path="a.py"))
    elapsed = time.monotonic() - started

    assert result.failed is True
    assert agent.calls == 5, "one try plus four retries — the old budget was nine"
    assert workflow._cooldown_until > started, "other agents must wait out the same window"
    assert elapsed < 5
    assert waits and waits[0] == 0.0, "the first agent did not wait before its first call"
