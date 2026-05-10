"""Unit tests for CodeGuardian agents."""

import pytest
from codeguardian.agents.base import (
    BaseReviewAgent,
    ReviewFinding,
    Severity,
    AgentResult,
)
from codeguardian.agents.style_agent import StyleAgent
from codeguardian.agents.security_agent import SecurityAgent
from codeguardian.agents.performance_agent import PerformanceAgent
from codeguardian.agents.logic_agent import LogicAgent
from codeguardian.agents.repo_agent import RepoAgent
from codeguardian.agents.refactor_agent import RefactorAgent
from codeguardian.agents.fix_agent import FixAgent
from codeguardian.agents.test_agent import TestAgent
from codeguardian.agents.doc_agent import DocAgent
from codeguardian.agents.coordinator import CoordinatorAgent


# ---------------------------------------------------------------------------
# Dataclass / data model tests
# ---------------------------------------------------------------------------


def test_review_finding_creation():
    finding = ReviewFinding(
        agent="SecurityAgent",
        severity=Severity.HIGH,
        title="SQL Injection",
        description="User input directly interpolated into SQL query",
        line_number=10,
        suggestion="Use parameterized queries",
    )
    assert finding.agent == "SecurityAgent"
    assert finding.severity == Severity.HIGH
    assert finding.line_number == 10


def test_agent_result_defaults():
    result = AgentResult(agent_name="Test")
    assert result.findings == []
    assert result.summary == ""
    assert result.token_usage == 0


def test_severity_enum():
    assert Severity.LOW.value == "low"
    assert Severity.MEDIUM.value == "medium"
    assert Severity.HIGH.value == "high"
    assert Severity.CRITICAL.value == "critical"


# ---------------------------------------------------------------------------
# Agent instantiation tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_cls, expected_name",
    [
        (StyleAgent, "StyleAgent"),
        (SecurityAgent, "SecurityAgent"),
        (PerformanceAgent, "PerformanceAgent"),
        (LogicAgent, "LogicAgent"),
        (RepoAgent, "RepoAgent"),
        (RefactorAgent, "RefactorAgent"),
        (FixAgent, "FixAgent"),
        (TestAgent, "TestAgent"),
        (DocAgent, "DocAgent"),
        (CoordinatorAgent, "CoordinatorAgent"),
    ],
)
def test_agent_instantiation(agent_cls, expected_name):
    agent = agent_cls(model="mimo-v2.5-pro", api_key="test-key")
    assert agent.name == expected_name
    assert isinstance(agent, BaseReviewAgent)


# ---------------------------------------------------------------------------
# System prompt tests (no API call needed)
# ---------------------------------------------------------------------------


def test_style_agent_prompt():
    agent = StyleAgent(model="mimo-v2.5-pro", api_key="test-key")
    prompt = agent.get_system_prompt()
    assert "code style" in prompt.lower()
    assert "naming" in prompt.lower()


def test_security_agent_prompt():
    agent = SecurityAgent(model="mimo-v2.5-pro", api_key="test-key")
    prompt = agent.get_system_prompt()
    assert "injection" in prompt.lower()
    assert "authentication" in prompt.lower()


def test_performance_agent_prompt():
    agent = PerformanceAgent(model="mimo-v2.5-pro", api_key="test-key")
    prompt = agent.get_system_prompt()
    assert "performance" in prompt.lower() or "n+1" in prompt.lower()


def test_logic_agent_prompt():
    agent = LogicAgent(model="mimo-v2.5-pro", api_key="test-key")
    prompt = agent.get_system_prompt()
    assert "edge case" in prompt.lower() or "error handling" in prompt.lower()


def test_all_agents_have_unique_focus():
    """Ensure every agent declares a distinct review focus."""
    agents = [
        StyleAgent(model="mimo-v2.5-pro", api_key="test-key"),
        SecurityAgent(model="mimo-v2.5-pro", api_key="test-key"),
        PerformanceAgent(model="mimo-v2.5-pro", api_key="test-key"),
        LogicAgent(model="mimo-v2.5-pro", api_key="test-key"),
        RepoAgent(model="mimo-v2.5-pro", api_key="test-key"),
        RefactorAgent(model="mimo-v2.5-pro", api_key="test-key"),
        FixAgent(model="mimo-v2.5-pro", api_key="test-key"),
        TestAgent(model="mimo-v2.5-pro", api_key="test-key"),
        DocAgent(model="mimo-v2.5-pro", api_key="test-key"),
    ]
    foci = [a.get_review_focus() for a in agents]
    assert len(foci) == len(set(foci))  # no duplicates


# ---------------------------------------------------------------------------
# Parse findings tests
# ---------------------------------------------------------------------------


SAMPLE_RESPONSE = """- Severity: HIGH
- Title: SQL Injection Risk
- Line: 12
- Description: User input directly interpolated into SQL query.
- Suggestion: Use parameterized queries with placeholder syntax.

- Severity: LOW
- Title: Missing docstring
- Line: 25
- Description: The function has no docstring.
- Suggestion: Add a docstring describing parameters and return value.
"""


def test_parse_findings():
    agent = StyleAgent(model="mimo-v2.5-pro", api_key="test-key")
    findings = agent._parse_findings(SAMPLE_RESPONSE)
    assert len(findings) == 2
    assert findings[0].severity == Severity.HIGH
    assert findings[0].title == "SQL Injection Risk"
    assert findings[0].line_number == 12
    assert findings[1].severity == Severity.LOW


def test_parse_findings_empty():
    agent = SecurityAgent(model="mimo-v2.5-pro", api_key="test-key")
    findings = agent._parse_findings("No issues found.")
    # Fallback: non-empty text is wrapped as a single finding so output is never lost
    assert len(findings) == 1
    assert findings[0].severity == Severity.LOW
    assert "No issues found" in findings[0].title


def test_parse_findings_truly_empty():
    agent = SecurityAgent(model="mimo-v2.5-pro", api_key="test-key")
    findings = agent._parse_findings("")
    assert findings == []


# ---------------------------------------------------------------------------
# Coordinator tests (no LLM needed)
# ---------------------------------------------------------------------------


def test_coordinator_synthesize():
    coordinator = CoordinatorAgent(model="mimo-v2.5-pro", api_key="test-key")
    results = [
        AgentResult(
            agent_name="SecurityAgent",
            findings=[
                ReviewFinding(
                    agent="SecurityAgent",
                    severity=Severity.HIGH,
                    title="SQL Injection",
                    description="...",
                ),
            ],
            summary="Done",
            token_usage=1000,
        ),
        AgentResult(
            agent_name="StyleAgent",
            findings=[
                ReviewFinding(
                    agent="StyleAgent",
                    severity=Severity.LOW,
                    title="Missing docstring",
                    description="...",
                ),
            ],
            summary="Done",
            token_usage=500,
        ),
        AgentResult(
            agent_name="SecurityAgent",
            findings=[
                ReviewFinding(
                    agent="SecurityAgent",
                    severity=Severity.HIGH,
                    title="SQL Injection",  # duplicate
                    description="...",
                ),
            ],
            summary="Done",
            token_usage=300,
        ),
    ]

    import asyncio
    result = asyncio.run(coordinator.synthesize(results))

    assert result.agent_name == "CoordinatorAgent"
    assert len(result.findings) == 2  # duplicates removed
    assert result.token_usage == 1800  # 1000 + 500 + 300
    assert "SQL Injection" in result.findings[0].title  # HIGH comes first


def test_coordinator_empty_input():
    coordinator = CoordinatorAgent(model="mimo-v2.5-pro", api_key="test-key")
    import asyncio
    result = asyncio.run(coordinator.synthesize([]))
    assert result.agent_name == "CoordinatorAgent"
    assert result.findings == []
    assert result.token_usage == 0


# ---------------------------------------------------------------------------
# Build human prompt tests
# ---------------------------------------------------------------------------


def test_build_human_prompt():
    agent = StyleAgent(model="mimo-v2.5-pro", api_key="test-key")
    prompt = agent._build_human_prompt("def foo(): pass", "test.py")
    assert "test.py" in prompt
    assert "def foo(): pass" in prompt
    assert agent.get_review_focus() in prompt


# ---------------------------------------------------------------------------
# MiMo endpoint compatibility tests
# ---------------------------------------------------------------------------


def test_mimo_base_url_accepted():
    """Verify agents accept a MiMo-compatible base_url."""
    agent = SecurityAgent(
        model="mimo-v2.5-pro",
        base_url="https://api.xiaomimimo.com/v1",
        api_key="test-key",
    )
    assert str(agent.llm.openai_api_base) == "https://api.xiaomimimo.com/v1"
    assert agent.llm.model_name == "mimo-v2.5-pro"


def test_deepseek_base_url_accepted():
    """Verify agents accept a DeepSeek-compatible base_url."""
    agent = SecurityAgent(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="test-key",
    )
    assert str(agent.llm.openai_api_base) == "https://api.deepseek.com/v1"


def test_agent_placeholder_api_key():
    """When no api_key provided, a ValueError is raised."""
    with pytest.raises(ValueError, match="API key is required"):
        StyleAgent(model="mimo-v2.5-pro")
