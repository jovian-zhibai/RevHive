"""Tests for deduplication and sorting utilities."""

import pytest
from revhive.agents.base import ReviewFinding, Severity
from revhive.utils.dedup import _extract_keywords, _jaccard_similarity, deduplicate_and_sort


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_basic_extraction(self):
        kw = _extract_keywords("SQL Injection via string interpolation")
        assert "sql" in kw
        assert "injection" in kw
        assert "string" in kw
        assert "interpolation" in kw

    def test_stop_words_removed(self):
        kw = _extract_keywords("the function is called by the user")
        assert "the" not in kw
        assert "is" not in kw
        assert "by" not in kw
        assert "function" in kw
        assert "called" in kw
        assert "user" in kw

    def test_short_words_removed(self):
        kw = _extract_keywords("a b cd ef ghij")
        assert "a" not in kw
        assert "b" not in kw
        assert "cd" not in kw  # len <= 2
        assert "ef" not in kw
        assert "ghij" in kw  # len > 2

    def test_empty_string(self):
        assert _extract_keywords("") == set()

    def test_numbers_included(self):
        kw = _extract_keywords("error code 404")
        assert "404" in kw

    def test_underscores_included(self):
        kw = _extract_keywords("get_user_by_id function")
        assert "get_user_by_id" in kw


# ---------------------------------------------------------------------------
# _jaccard_similarity
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert _jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard_similarity({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        sim = _jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        assert sim == pytest.approx(2 / 4)  # intersection=2, union=4

    def test_both_empty(self):
        assert _jaccard_similarity(set(), set()) == 1.0

    def test_one_empty(self):
        assert _jaccard_similarity({"a"}, set()) == 0.0
        assert _jaccard_similarity(set(), {"a"}) == 0.0

    def test_subset(self):
        sim = _jaccard_similarity({"a", "b"}, {"a", "b", "c", "d"})
        assert sim == pytest.approx(2 / 4)


# ---------------------------------------------------------------------------
# deduplicate_and_sort
# ---------------------------------------------------------------------------


def _make_finding(title, severity=Severity.MEDIUM, description="", file_path=None, line_number=None, agent="TestAgent"):
    return ReviewFinding(
        agent=agent,
        severity=severity,
        title=title,
        description=description,
        file_path=file_path,
        line_number=line_number,
    )


class TestDeduplicateAndSort:
    def test_empty_list(self):
        assert deduplicate_and_sort([]) == []

    def test_no_duplicates(self):
        findings = [
            _make_finding("Issue A", Severity.HIGH),
            _make_finding("Issue B", Severity.LOW),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 2

    def test_exact_title_dedup(self):
        findings = [
            _make_finding("SQL Injection", Severity.MEDIUM, "desc A", agent="Agent1"),
            _make_finding("SQL Injection", Severity.HIGH, "desc B", agent="Agent2"),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH

    def test_exact_title_case_insensitive(self):
        findings = [
            _make_finding("sql injection", Severity.LOW),
            _make_finding("SQL Injection", Severity.HIGH),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH

    def test_file_line_dedup(self):
        findings = [
            _make_finding("Issue A", Severity.LOW, file_path="app.py", line_number=10, agent="Agent1"),
            _make_finding("Issue B", Severity.HIGH, file_path="app.py", line_number=10, agent="Agent2"),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH

    def test_file_line_different_files_no_dedup(self):
        findings = [
            _make_finding("Issue A", Severity.HIGH, file_path="a.py", line_number=10),
            _make_finding("Issue B", Severity.HIGH, file_path="b.py", line_number=10),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 2

    def test_file_line_different_lines_no_dedup(self):
        findings = [
            _make_finding("Issue A", Severity.HIGH, file_path="a.py", line_number=10),
            _make_finding("Issue B", Severity.HIGH, file_path="a.py", line_number=20),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 2

    def test_semantic_similarity_dedup(self):
        findings = [
            _make_finding("SQL Injection via f-string", Severity.HIGH, "User input interpolated into SQL query"),
            _make_finding("SQL Injection via string formatting", Severity.MEDIUM, "User-controlled input in SQL"),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 1
        assert result[0].severity == Severity.HIGH

    def test_severity_ordering(self):
        findings = [
            _make_finding("Low issue", Severity.LOW),
            _make_finding("Critical issue", Severity.CRITICAL),
            _make_finding("Medium issue", Severity.MEDIUM),
            _make_finding("High issue", Severity.HIGH),
        ]
        result = deduplicate_and_sort(findings)
        assert result[0].severity == Severity.CRITICAL
        assert result[1].severity == Severity.HIGH
        assert result[2].severity == Severity.MEDIUM
        assert result[3].severity == Severity.LOW

    def test_findings_without_file_path_preserved(self):
        findings = [
            _make_finding("Issue A", Severity.HIGH, file_path=None, line_number=None),
            _make_finding("Issue B", Severity.MEDIUM, file_path=None, line_number=None),
        ]
        result = deduplicate_and_sort(findings)
        assert len(result) == 2

    def test_mixed_with_and_without_file_path(self):
        findings = [
            _make_finding("Issue A", Severity.HIGH, file_path="app.py", line_number=10),
            _make_finding("Issue B", Severity.MEDIUM, file_path=None, line_number=None),
            _make_finding("Issue C", Severity.LOW, file_path="app.py", line_number=10),
        ]
        result = deduplicate_and_sort(findings)
        # A and C have same file:line, keep A (higher severity). B preserved.
        assert len(result) == 2
        assert result[0].severity == Severity.HIGH
        assert result[1].severity == Severity.MEDIUM
