"""Deterministic diff anchoring for review findings.

Diff-mode reviews hand every agent ``file_path="diff:HEAD~1"``, so
findings come back anchored to that marker (or to nothing) instead of the
file they point at. This module recovers the real path from the diff
itself: it parses each file's hunk ranges and assigns findings to files
by line number — no LLM involved, same diff in, same anchors out.
"""

from __future__ import annotations

import re

# --- a/path  /  +++ b/path  (path may contain spaces; /dev/null = absent side)
_OLD_FILE_RE = re.compile(r"^--- (.+)$")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (.+)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _strip_prefix(path: str) -> str | None:
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def parse_diff_files(diff_text: str) -> dict[str, dict]:
    """Map each file in a unified diff to its line ranges.

    Returns ``{path: {"new": [(start, end), ...], "old": [(start, end), ...]}}``
    where ranges are 1-based inclusive hunk spans on the new and old side.
    """
    files: dict[str, dict] = {}
    current: str | None = None
    pending_old: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # A new file section resets everything: without this, a pure
            # deletion after another file's hunks would leak ranges over.
            current = None
            pending_old = None
            continue
        if line.startswith("--- "):
            m = _OLD_FILE_RE.match(line)
            old = _strip_prefix(m.group(1)) if m else None
            pending_old = old if old and old != "/dev/null" else None
            continue
        m = _NEW_FILE_RE.match(line)
        if m:
            target = _strip_prefix(m.group(1))
            if target == "/dev/null":
                # Pure deletion: anchor to the old path so removed code
                # stays attributable.
                current = pending_old
                if current:
                    files.setdefault(current, {"new": [], "old": []})
            else:
                current = target
                files.setdefault(current, {"new": [], "old": []})
            continue
        if line.startswith("@@ ") and current:
            m = _HUNK_RE.match(line)
            if m:
                new_start = int(m.group(3))
                new_count = int(m.group(4) if m.group(4) is not None else 1)
                old_start = int(m.group(1))
                old_count = int(m.group(2) if m.group(2) is not None else 1)
                if new_count > 0:
                    files[current]["new"].append((new_start, new_start + new_count - 1))
                if old_count > 0:
                    files[current]["old"].append((old_start, old_start + old_count - 1))
            continue
    return files


def _in_ranges(line: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in ranges)


def _distance(line: int, ranges: list[tuple[int, int]]) -> int:
    return min((min(abs(line - start), abs(line - end)) for start, end in ranges), default=10**9)


def anchor_diff_findings(findings: list, diff_text: str) -> tuple[list, list[str]]:
    """Fill in real file paths for findings reviewed from a diff.

    A finding whose ``file_path`` is empty or a ``diff:`` marker is anchored
    by line number to the file whose hunk contains it (new side first, then
    old side — deleted lines are attributed too). Ambiguous matches pick the
    nearest hunk. A finding with no line number is anchored only when the
    diff touches exactly one file. Never overwrites a real path, never
    changes severity, never drops a finding.
    """
    files = parse_diff_files(diff_text)
    if not files:
        return findings, []

    adjustments: list[str] = []
    paths = sorted(files)
    for f in findings:
        fp = getattr(f, "file_path", None)
        if fp and not fp.startswith("diff:"):
            continue
        line = getattr(f, "line_number", None)
        target: str | None = None
        if line:
            containing = [p for p in paths
                          if _in_ranges(line, files[p]["new"]) or _in_ranges(line, files[p]["old"])]
            if len(containing) == 1:
                target = containing[0]
            elif containing:
                target = min(containing, key=lambda p: _distance(line, files[p]["new"] or files[p]["old"]))
            elif len(paths) == 1:
                # Line math didn't match any hunk (off-by-N estimates happen),
                # but with one file in the diff there is nothing else it can be.
                target = paths[0]
        elif len(paths) == 1:
            target = paths[0]
        if target and target != fp:
            f.file_path = target
            adjustments.append(f"anchored to {target}:{line or '?'}: {f.title[:60]}")
    return findings, adjustments
