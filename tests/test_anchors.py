"""Tests for deterministic diff anchoring."""

from revhive.agents.base import ReviewFinding, Severity
from revhive.utils.anchors import anchor_diff_findings, parse_diff_files

DIFF = """diff --git a/src/http/client.py b/src/http/client.py
index 1111..2222 100644
--- a/src/http/client.py
+++ b/src/http/client.py
@@ -10,7 +10,7 @@ def request(self):
-    old_line_12()
+    new_line_12()
@@ -100,3 +100,4 @@ def other():
+    added_at_100()
diff --git a/src/http/auth.py b/src/http/auth.py
index 3333..4444 100644
--- a/src/http/auth.py
+++ b/src/http/auth.py
@@ -50,2 +50,3 @@ def login():
+    added_at_50()
diff --git a/src/legacy/deleted.py b/src/legacy/deleted.py
deleted file mode 100644
--- a/src/legacy/deleted.py
+++ /dev/null
@@ -1,5 +0,0 @@
-removed_code_line_3()
"""


def _finding(severity="high", title="bug", file_path=None, line_number=None):
    return ReviewFinding(agent="SecurityAgent", severity=Severity(severity), title=title,
                         description="d", file_path=file_path, line_number=line_number)


class TestParseDiffFiles:
    def test_multiple_files_with_ranges(self):
        files = parse_diff_files(DIFF)
        assert set(files) == {"src/http/client.py", "src/http/auth.py", "src/legacy/deleted.py"}
        assert (10, 16) in files["src/http/client.py"]["new"]
        assert (100, 103) in files["src/http/client.py"]["new"]
        assert (50, 52) in files["src/http/auth.py"]["new"]

    def test_deleted_file_uses_old_ranges(self):
        files = parse_diff_files(DIFF)
        deleted = files["src/legacy/deleted.py"]
        assert deleted["new"] == []
        assert (1, 5) in deleted["old"]


class TestAnchorDiffFindings:
    def test_line_in_hunk_gets_real_path(self):
        f = _finding(line_number=12)
        out, log = anchor_diff_findings([f], DIFF)
        assert out[0].file_path == "src/http/client.py"
        assert len(log) == 1

    def test_deleted_line_attributed_to_old_file(self):
        f = _finding(line_number=3)
        out, _ = anchor_diff_findings([f], DIFF)
        assert out[0].file_path == "src/legacy/deleted.py"

    def test_no_line_single_file_anchors(self):
        diff = DIFF.split("diff --git a/src/http/auth.py")[0]
        f = _finding(line_number=None)
        out, _ = anchor_diff_findings([f], diff)
        assert out[0].file_path == "src/http/client.py"

    def test_no_line_multi_file_stays_unanchored(self):
        f = _finding(line_number=None)
        out, log = anchor_diff_findings([f], DIFF)
        assert out[0].file_path is None
        assert log == []

    def test_real_path_never_overwritten(self):
        f = _finding(file_path="src/other.py", line_number=12)
        out, log = anchor_diff_findings([f], DIFF)
        assert out[0].file_path == "src/other.py"
        assert log == []

    def test_junk_diff_marker_replaced(self):
        f = _finding(file_path="diff:HEAD~1", line_number=50)
        out, _ = anchor_diff_findings([f], DIFF)
        assert out[0].file_path == "src/http/auth.py"

    def test_off_by_n_line_falls_back_to_single_file(self):
        diff = DIFF.split("diff --git a/src/http/auth.py")[0]
        f = _finding(line_number=999)
        out, _ = anchor_diff_findings([f], diff)
        assert out[0].file_path == "src/http/client.py"

    def test_never_drops_or_rescores(self):
        findings = [_finding(line_number=12), _finding(line_number=50)]
        out, _ = anchor_diff_findings(findings, DIFF)
        assert len(out) == 2
        assert all(f.severity == Severity.HIGH for f in out)
