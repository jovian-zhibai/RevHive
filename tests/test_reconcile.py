"""Tests for deterministic severity reconciliation."""

import pytest

from revhive.agents.base import ReviewFinding, Severity
from revhive.utils.reconcile import (
    _findings_match,
    _median_severity,
    consensus_summary_block,
    reconcile_findings,
    reconcile_passes,
)


def _finding(severity="medium", title="SQL injection in query", description="desc",
             file_path="src/db.py", line_number=10, agent="SecurityAgent"):
    return ReviewFinding(
        agent=agent,
        severity=Severity(severity),
        title=title,
        description=description,
        file_path=file_path,
        line_number=line_number,
    )


# ---------------------------------------------------------------------------
# reconcile_findings: in-run caps
# ---------------------------------------------------------------------------


class TestReconcileFindings:
    def test_hedged_title_caps_high(self):
        f = _finding("high", title="This may bypass authentication checks")
        out, log = reconcile_findings([f])
        assert out[0].severity == Severity.MEDIUM
        assert len(log) == 1
        assert "hedged title" in log[0]

    def test_hedged_title_caps_critical(self):
        f = _finding("critical", title="Possible path traversal via user input")
        out, _ = reconcile_findings([f])
        assert out[0].severity == Severity.MEDIUM

    def test_prose_hedging_does_not_cap(self):
        # "could allow an attacker" in the *description* is how security
        # findings are written; only a hedged title counts.
        f = _finding("high", title="SQL injection in query builder",
                     description="This could allow an attacker to read arbitrary rows.")
        out, log = reconcile_findings([f])
        assert out[0].severity == Severity.HIGH
        assert log == []

    def test_unanchored_finding_capped(self):
        f = _finding("critical", title="SSRF via proxy", file_path=None, line_number=None)
        out, log = reconcile_findings([f])
        assert out[0].severity == Severity.MEDIUM
        assert "no code anchor" in log[0]

    def test_file_only_anchor_survives(self):
        f = _finding("critical", title="SSRF via proxy", file_path="src/net.py", line_number=None)
        out, log = reconcile_findings([f])
        assert out[0].severity == Severity.CRITICAL
        assert log == []

    def test_never_upgrades(self):
        f = _finding("low", title="May be an issue")
        out, _ = reconcile_findings([f])
        assert out[0].severity == Severity.LOW

    def test_never_drops(self):
        findings = [_finding("critical"), _finding("low")]
        out, _ = reconcile_findings(findings)
        assert len(out) == 2

    def test_no_adjustments_returns_empty_log(self):
        out, log = reconcile_findings([_finding("medium")])
        assert log == []
        assert out[0].severity == Severity.MEDIUM


# ---------------------------------------------------------------------------
# cross-pass consensus
# ---------------------------------------------------------------------------


class TestMedianSeverity:
    def test_odd_count_takes_middle(self):
        assert _median_severity([Severity.HIGH, Severity.CRITICAL, Severity.LOW]) == Severity.HIGH

    def test_even_count_takes_quieter(self):
        assert _median_severity([Severity.CRITICAL, Severity.MEDIUM]) == Severity.MEDIUM


class TestFindingsMatch:
    def test_same_file_nearby_line_similar_title(self):
        a = _finding(line_number=100, title="SQL injection in get_user")
        b = _finding(line_number=102, title="SQL injection in get_user query")
        assert _findings_match(a, b)

    def test_different_file_never_matches(self):
        a = _finding(file_path="src/a.py", title="SQL injection")
        b = _finding(file_path="src/b.py", title="SQL injection")
        assert not _findings_match(a, b)

    def test_far_lines_do_not_match(self):
        a = _finding(line_number=10, title="SQL injection in get_user")
        b = _finding(line_number=400, title="SQL injection in get_user")
        assert not _findings_match(a, b)


class TestReconcilePasses:
    def test_single_pass_returns_capped_findings(self):
        out, stats = reconcile_passes([[_finding("high", title="May break things")]])
        assert out[0].severity == Severity.MEDIUM
        assert stats["passes"] == 1
        assert stats["disagreements"] == 0

    def test_matching_group_takes_median(self):
        p1 = [_finding("critical", title="SQL injection in get_user", line_number=10)]
        p2 = [_finding("medium", title="SQL injection in get_user", line_number=11)]
        out, stats = reconcile_passes([p1, p2])
        assert stats["groups"] == 1
        assert stats["disagreements"] == 1
        assert out[0].severity == Severity.MEDIUM  # median of 2 = quieter

    def test_singleton_capped_one_level(self):
        p1 = [_finding("high", title="Totally unique defect alpha")]
        p2 = []
        out, stats = reconcile_passes([p1, p2])
        assert stats["singletons_capped"] == 1
        assert out[0].severity == Severity.MEDIUM

    def test_nothing_dropped(self):
        p1 = [_finding("high", title="defect one", line_number=10),
              _finding("low", title="defect two", line_number=20)]
        p2 = [_finding("medium", title="completely unrelated header issue", line_number=200,
                       file_path="src/other.py")]
        out, _ = reconcile_passes([p1, p2])
        assert len(out) == 3

    def test_empty_passes(self):
        out, stats = reconcile_passes([])
        assert out == []
        assert stats["groups"] == 0

    def test_agent_names_merged(self):
        p1 = [_finding("high", title="SQL injection in get_user", agent="SecurityAgent")]
        p2 = [_finding("low", title="SQL injection in get_user", agent="LogicAgent")]
        out, _ = reconcile_passes([p1, p2])
        assert "SecurityAgent" in out[0].agent
        assert "LogicAgent" in out[0].agent


# ---------------------------------------------------------------------------
# summary block
# ---------------------------------------------------------------------------


class TestConsensusSummaryBlock:
    def test_single_pass_no_adjustments_is_empty(self):
        assert consensus_summary_block({"passes": 1, "adjustments": []}) == ""

    def test_single_pass_with_adjustments(self):
        block = consensus_summary_block({"passes": 1, "adjustments": ["capped HIGH->MEDIUM"]})
        assert "1 severity cap" in block

    def test_multi_pass_discloses_disagreement(self):
        block = consensus_summary_block({
            "passes": 3, "groups": 5, "disagreements": 2,
            "singletons_capped": 1, "adjustments": ["x"],
        })
        assert "3 independent passes" in block
        assert "2 disagreed" in block
        assert "capped one level down" in block
