"""Merging findings that different agents wrote up as the same defect.

Word-level dedup cannot do this: "Unhandled None from fetchone when user not
found" and "Handle missing user record to prevent crash" share one word out of
twelve, while a SQL injection and a missing auth check on the same line share
two. The coordinator asks the model instead — and must never trust a bad
answer, because a wrong merge hides a defect from the customer.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from revhive.agents.base import ReviewFinding, Severity
from revhive.agents.coordinator import CoordinatorAgent


def _finding(title, line, severity=Severity.HIGH, agent="LogicAgent", description="desc"):
    return ReviewFinding(
        agent=agent,
        severity=severity,
        title=title,
        description=description,
        file_path="app.py",
        line_number=line,
    )


def _coordinator(reply: str):
    coordinator = CoordinatorAgent.__new__(CoordinatorAgent)
    coordinator.llm = AsyncMock()
    coordinator.llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=reply))
    return coordinator


def _run(coordinator, findings):
    return asyncio.run(coordinator._consolidate(findings))


def test_same_defect_from_two_agents_becomes_one_finding():
    findings = [
        _finding("Unhandled None from fetchone", 30, Severity.CRITICAL, "LogicAgent", "row is None"),
        _finding("Handle missing user record", 20, Severity.HIGH, "FixAgent", "row[0] crashes"),
        _finding("Missing authentication", 8, Severity.HIGH, "SecurityAgent", "no auth check"),
        _finding("Unclosed connection", 2, Severity.MEDIUM, "PerformanceAgent", "leaks handles"),
    ]
    coordinator = _coordinator('{"groups": [[0, 1], [2], [3]]}')

    out, absorbed = _run(coordinator, findings)

    assert absorbed == 1
    assert len(out) == 3
    merged = out[0]
    assert merged.severity == Severity.CRITICAL, "highest severity survives"
    assert merged.line_number == 20, "earliest line survives"
    assert "LogicAgent" in merged.agent and "FixAgent" in merged.agent
    assert "row[0] crashes" in merged.description, "the other write-up is kept, not dropped"


def test_different_defects_on_nearby_lines_are_not_merged():
    findings = [
        _finding("SQL injection in /user", 40, Severity.CRITICAL, "SecurityAgent"),
        _finding("Missing authentication on /user", 20, Severity.HIGH, "SecurityAgent"),
        _finding("Unclosed connection", 4, Severity.HIGH, "PerformanceAgent"),
    ]
    coordinator = _coordinator('{"groups": [[0], [1], [2]]}')

    out, absorbed = _run(coordinator, findings)

    assert absorbed == 0
    assert len(out) == 3


def test_unparseable_answer_leaves_the_list_alone():
    findings = [_finding(f"Issue {i}", i * 10 + 1) for i in range(4)]
    for reply in ("I think they are all one issue", "{oops}", ""):
        coordinator = _coordinator(reply)
        out, absorbed = _run(coordinator, findings)
        assert out == findings
        assert absorbed == 0


def test_partition_that_drops_a_finding_is_rejected():
    """Silently losing an index would hide a defect the customer never sees."""
    findings = [_finding(f"Issue {i}", i * 10 + 1) for i in range(4)]
    coordinator = _coordinator('{"groups": [[0, 1], [2]]}')  # index 3 missing

    out, absorbed = _run(coordinator, findings)

    assert out == findings
    assert absorbed == 0


@pytest.mark.parametrize(
    "reply",
    [
        '{"groups": [[0, 1], [1], [2], [3]]}',  # index repeated
        '{"groups": [[0, 1], [2], [3], [9]]}',  # out of range
        '{"groups": []}',  # nothing at all
        '{"groups": [[0], [1], [2], ["3"]]}',  # not an int
    ],
)
def test_malformed_partitions_are_rejected(reply):
    findings = [_finding(f"Issue {i}", i * 10 + 1) for i in range(4)]
    coordinator = _coordinator(reply)

    out, absorbed = _run(coordinator, findings)

    assert out == findings
    assert absorbed == 0


def test_model_failure_is_not_fatal():
    """A throttled provider must not cost the customer their report."""
    findings = [_finding(f"Issue {i}", i * 10 + 1) for i in range(4)]
    coordinator = CoordinatorAgent.__new__(CoordinatorAgent)
    coordinator.llm = AsyncMock()
    coordinator.llm.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch("revhive.agents.coordinator.asyncio.sleep", AsyncMock()):
        out, absorbed = _run(coordinator, findings)

    assert out == findings
    assert absorbed == 0
    assert coordinator.llm.ainvoke.await_count == 2, "one retry, then give up"


def test_small_reviews_skip_the_extra_model_call():
    findings = [_finding("A", 10), _finding("B", 20), _finding("C", 30)]
    coordinator = _coordinator('{"groups": [[0], [1], [2]]}')

    out, absorbed = _run(coordinator, findings)

    assert out == findings
    coordinator.llm.ainvoke.assert_not_awaited()


class TestSameLineFallback:
    """When the model cannot answer, adjacent-line duplicates still collapse.

    Two inline comments on consecutive lines look like noise even when the
    wording differs, and that is the case a customer notices first.
    """

    def _fallback(self, findings):
        coordinator = CoordinatorAgent.__new__(CoordinatorAgent)
        coordinator.llm = AsyncMock()
        coordinator.llm.ainvoke = AsyncMock(side_effect=RuntimeError("throttled"))
        with patch("revhive.agents.coordinator.asyncio.sleep", AsyncMock()):
            return _run(coordinator, findings)

    def test_neighbouring_lines_merge_and_keep_the_worst_severity(self):
        findings = [
            _finding("Unhandled None from fetchone", 16, Severity.CRITICAL, "LogicAgent", "row is None"),
            _finding("Handle missing user record", 17, Severity.MEDIUM, "FixAgent", "row[0] crashes"),
            _finding("Missing authentication", 8, Severity.HIGH, "SecurityAgent", "no auth"),
        ]

        out, absorbed = self._fallback(findings)

        assert absorbed == 1
        assert len(out) == 2
        assert out[0].severity == Severity.CRITICAL
        assert "row[0] crashes" in out[0].description, "nothing is dropped"

    def test_lines_further_apart_are_left_alone(self):
        findings = [
            _finding("Unhandled None from fetchone", 16, Severity.CRITICAL, "LogicAgent"),
            _finding("Handle missing user record", 18, Severity.MEDIUM, "FixAgent"),
            _finding("Missing authentication", 8, Severity.HIGH, "SecurityAgent"),
            _finding("Unclosed connection", 2, Severity.HIGH, "PerformanceAgent"),
        ]

        out, absorbed = self._fallback(findings)

        assert absorbed == 0
        assert len(out) == 4
