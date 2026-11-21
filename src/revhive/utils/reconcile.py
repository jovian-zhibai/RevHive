"""Deterministic severity reconciliation.

Asking the model to "be conservative" in prose proved unreliable: paired
reruns of the same 19 commits with the same model and the same prompt
produced risk-weighted scores of 45 vs 54 across passes — sampling
variance exceeded the effect of any wording change. Severity is therefore
reconciled in code, deterministically, in two layers:

1. In-run caps (:func:`reconcile_findings`) — rules calibrated against
   106 stored runs. A hedged *title* never belonged to a HIGH/CRITICAL
   finding that survived ground truth, and a finding with no code anchor
   cannot be verified by a human reader, so it cannot claim HIGH.

2. Cross-pass consensus (:func:`reconcile_passes`) — when the review runs
   more than once, each matched group of findings takes the median
   severity and the report states the disagreement rate instead of
   hiding it.

Everything here is pure functions over findings: no LLM calls, same input
always produces the same output.
"""

from __future__ import annotations

import re
from dataclasses import replace

from revhive.agents.base import SEVERITY_ORDER, ReviewFinding, Severity
from revhive.utils.dedup import _extract_keywords, _jaccard_similarity

#: A hedged *title* is a different signal from hedged prose. "Could allow
#: an attacker" is how security findings are written; a title that says
#: "may bypass" means the agent itself is not asserting the harm. Across
#: 106 stored runs, zero HIGH/CRITICAL findings that matched ground truth
#: carried a hedged title.
_TITLE_HEDGE_RE = re.compile(
    r"\b(may|might|could|possibly|potential|potentially|possible)\b", re.IGNORECASE
)

#: Same thresholds as merge_adjacent(): an off-by-one in the reported line
#: is the usual reason two passes disagree on the number.
_MATCH_LINE_PROXIMITY = 3
_MATCH_TITLE_SIMILARITY = 0.34

_ORDER = ["critical", "high", "medium", "low"]


def _cap(severity: Severity, to: str) -> Severity:
    """Return *severity* capped at *to* (never upgraded)."""
    if SEVERITY_ORDER.get(severity.value, 99) < SEVERITY_ORDER.get(to, 99):
        return Severity(to)
    return severity


def reconcile_findings(
    findings: list[ReviewFinding],
) -> tuple[list[ReviewFinding], list[str]]:
    """Apply deterministic in-run severity caps.

    Returns the reconciled findings and a log of adjustments (empty when
    nothing changed). Never upgrades a severity and never drops a finding:
    a missed cap is noise, a dropped finding is a defect the user never
    sees.
    """
    adjustments: list[str] = []
    out: list[ReviewFinding] = []
    for f in findings:
        new = f
        # Rule 1: hedged title caps HIGH/CRITICAL at MEDIUM.
        if f.severity in (Severity.HIGH, Severity.CRITICAL) and _TITLE_HEDGE_RE.search(
            f.title or ""
        ):
            new = replace(new, severity=Severity.MEDIUM)
            adjustments.append(
                f"capped {f.severity.value.upper()}->MEDIUM (hedged title): {f.title[:70]}"
            )
        # Rule 2: no anchor, no HIGH. A finding with neither a file path nor
        # a line number points at nothing the user can open and verify. A
        # "diff:<ref>" marker is a review mode, not a path, so it does not
        # count as an anchor either.
        fp = new.file_path or ""
        if new.severity in (Severity.HIGH, Severity.CRITICAL) and (
            not fp or fp.startswith("diff:")
        ) and not new.line_number:
            new = replace(new, severity=Severity.MEDIUM)
            adjustments.append(
                f"capped {f.severity.value.upper()}->MEDIUM (no code anchor): {f.title[:70]}"
            )
        out.append(new)
    return out, adjustments


def _findings_match(a: ReviewFinding, b: ReviewFinding) -> bool:
    """True when two findings from different passes plausibly describe the
    same defect — same file, nearby lines, overlapping subject words."""
    if (a.file_path or None) != (b.file_path or None):
        return False
    if a.line_number and b.line_number:
        if abs(a.line_number - b.line_number) > _MATCH_LINE_PROXIMITY:
            return False
    elif a.line_number != b.line_number:
        # One pass anchored the finding, the other did not; same file plus
        # strong title overlap below still counts.
        if _jaccard_similarity(
            _extract_keywords(a.title or ""), _extract_keywords(b.title or "")
        ) < 0.6:
            return False
    sim = _jaccard_similarity(
        _extract_keywords(a.title or ""), _extract_keywords(b.title or "")
    )
    return sim >= _MATCH_TITLE_SIMILARITY


def _median_severity(severities: list[Severity]) -> Severity:
    """Median severity of a matched group, ties resolved conservatively.

    For an odd count this is the middle value. For an even count (two
    passes disagreeing) it is the *lower* of the two claims: severity
    describes what the diff proves, and a defect one pass could not
    substantiate should not inherit the other pass's alarm.
    """
    ranked = sorted(severities, key=lambda s: SEVERITY_ORDER.get(s.value, 99))
    mid = len(ranked) // 2
    if len(ranked) % 2 == 1:
        return ranked[mid]
    return ranked[mid]  # even: the lower-severity of the two middle values


def reconcile_passes(
    passes: list[list[ReviewFinding]],
) -> tuple[list[ReviewFinding], dict]:
    """Merge findings from N review passes into one consensus list.

    Findings are matched across passes with :func:`_findings_match`;
    matched groups take the median severity. A finding reported in no
    more than half the passes is capped one level below its claim — it
    was not reproducible enough to inherit its full alarm. Nothing is
    dropped.

    Returns the consensus findings and a stats dict:
    ``passes``, ``groups``, ``disagreements`` (groups whose severity
    claims differed), ``singletons_capped``, ``adjustments`` (human-
    readable log lines).
    """
    n = len(passes)
    if n <= 1:
        findings = list(passes[0]) if passes else []
        findings, adjustments = reconcile_findings(findings)
        return findings, {"passes": n, "groups": len(findings),
                          "disagreements": 0, "singletons_capped": 0,
                          "adjustments": adjustments}

    # Greedy matching: walk every finding, collect all findings from the
    # other passes that describe the same defect.
    consumed: set[tuple[int, int]] = set()  # (pass_idx, finding_idx)
    groups: list[list[ReviewFinding]] = []

    for i, findings in enumerate(passes):
        for j, f in enumerate(findings):
            if (i, j) in consumed:
                continue
            group = [f]
            for k in range(i + 1, n):
                for l, g in enumerate(passes[k]):
                    if (k, l) in consumed:
                        continue
                    if _findings_match(f, g):
                        group.append(g)
                        consumed.add((k, l))
            consumed.add((i, j))
            groups.append(group)

    out: list[ReviewFinding] = []
    adjustments: list[str] = []
    disagreements = 0
    singletons_capped = 0

    for group in groups:
        claims = {g.severity for g in group}
        if len(claims) > 1:
            disagreements += 1
        median = _median_severity([g.severity for g in group])
        # Keep the most severe member's write-up: it is usually the most
        # complete claim, and the median severity above already governs
        # how alarmed the report should sound.
        primary = min(group, key=lambda g: SEVERITY_ORDER.get(g.severity.value, 99))
        if len(group) * 2 <= n:
            # Reported in a minority of passes: cap one level down.
            capped = {
                Severity.CRITICAL: Severity.HIGH,
                Severity.HIGH: Severity.MEDIUM,
                Severity.MEDIUM: Severity.LOW,
                Severity.LOW: Severity.LOW,
            }[median]
            if capped != median:
                adjustments.append(
                    f"capped {median.value.upper()}->{capped.value.upper()} "
                    f"(found in {len(group)}/{n} passes): {group[0].title[:70]}"
                )
                singletons_capped += 1
                median = capped
        elif median != primary.severity:
            adjustments.append(
                f"median {primary.severity.value.upper()}->{median.value.upper()} "
                f"(consensus of {len(group)}/{n} passes): {group[0].title[:70]}"
            )
        out.append(
            ReviewFinding(
                agent=" + ".join(dict.fromkeys(g.agent for g in group if g.agent)),
                severity=median,
                title=primary.title,
                description=primary.description,
                file_path=next((g.file_path for g in group if g.file_path), None),
                line_number=next((g.line_number for g in group if g.line_number), None),
                code_snippet=next((g.code_snippet for g in group if g.code_snippet), None),
                suggestion=next((g.suggestion for g in group if g.suggestion), None),
            )
        )

    # Sort most severe first, then apply in-run caps on the consensus list.
    out.sort(key=lambda f: SEVERITY_ORDER.get(f.severity.value, 99))
    out, cap_log = reconcile_findings(out)

    stats = {
        "passes": n,
        "groups": len(out),
        "disagreements": disagreements,
        "singletons_capped": singletons_capped,
        "adjustments": adjustments + cap_log,
    }
    return out, stats


def consensus_summary_block(stats: dict) -> str:
    """Human-readable note about the consensus process, appended to the report."""
    n = stats.get("passes", 1)
    if n <= 1:
        adjustments = stats.get("adjustments") or []
        if not adjustments:
            return ""
        lines = ["", f"🛡️ {len(adjustments)} severity cap(s) applied by deterministic post-processing:"]
        lines.extend(f"  - {a}" for a in adjustments[:5])
        return "\n".join(lines)
    lines = [
        "",
        f"🛡️ Consensus of {n} independent passes: "
        f"{stats.get('groups', 0)} matched groups, "
        f"{stats.get('disagreements', 0)} disagreed on severity "
        f"(median severity kept).",
    ]
    if stats.get("singletons_capped"):
        lines.append(
            f"  {stats['singletons_capped']} finding(s) appeared in a minority of passes "
            "and were capped one level down (not reproducible enough to inherit "
            "their full severity)."
        )
    adjustments = stats.get("adjustments") or []
    if adjustments:
        lines.append("  Adjustments:")
        lines.extend(f"  - {a}" for a in adjustments[:5])
    return "\n".join(lines)
