"""Coordinator agent that orchestrates the review process and synthesizes results."""

import asyncio
import logging
import re

from revhive.agents.base import SEVERITY_ORDER, AgentResult, BaseReviewAgent, ReviewFinding, Severity
from revhive.utils.dedup import _extract_keywords, _jaccard_similarity, deduplicate_and_sort
from revhive.utils.reconcile import consensus_summary_block, reconcile_findings

logger = logging.getLogger(__name__)


class CoordinatorAgent(BaseReviewAgent):
    """Coordinates multiple review agents and produces the final consolidated report."""

    def __init__(self, **kwargs):
        super().__init__(
            name="CoordinatorAgent",
            description="Orchestrates review agents and synthesizes findings into a final report",
            **kwargs,
        )

    def get_system_prompt(self) -> str:
        return """You are a code review coordinator. Your job is to:
1. Synthesize findings from multiple specialized review agents
2. Resolve conflicting assessments between agents
3. Prioritize findings by actual risk and impact
4. Produce a cohesive, actionable review report

When agents disagree on severity, err on the side of caution. Deduplicate related findings across agents."""

    def get_review_focus(self) -> str:
        return "synthesis, conflict resolution, prioritization, report generation"

    async def synthesize(self, results: list[AgentResult]) -> AgentResult:
        """Synthesize multiple agent results into a consolidated report."""
        all_findings = []
        for result in results:
            all_findings.extend(result.findings)

        deduplicated = deduplicate_and_sort(all_findings)

        # Resolve conflicts via LLM
        resolved, conflicts_resolved = await self._resolve_conflicts(deduplicated)

        # Word-level dedup cannot see that "Unhandled None from fetchone" and
        # "Handle missing user record" are one defect — they share one word out
        # of twelve. A customer reading 15 comments about 4 problems cancels,
        # so ask the model which findings are the same defect.
        resolved, merged_groups = await self._consolidate(resolved)
        conflicts_resolved += merged_groups

        # Deterministic severity reconciliation. Prompt-level instructions
        # to "be conservative" did not survive paired reruns — sampling
        # variance exceeded the effect of the wording. Caps are applied in
        # code instead: same input, same severities, every run.
        resolved, cap_log = reconcile_findings(resolved)

        risk_score = self._calculate_risk_score(resolved)
        summary = self._generate_summary(resolved, results, risk_score, conflicts_resolved)
        if cap_log:
            summary += consensus_summary_block({"passes": 1, "adjustments": cap_log})

        return AgentResult(
            agent_name="CoordinatorAgent",
            findings=resolved,
            summary=summary,
            token_usage=sum(r.token_usage for r in results),
            risk_score=risk_score,
        )

    _RISK_POINTS = {
        Severity.CRITICAL: 25,
        Severity.HIGH: 15,
        Severity.MEDIUM: 5,
        Severity.LOW: 1,
    }

    @staticmethod
    def _calculate_risk_score(findings: list[ReviewFinding]) -> int:
        """Calculate risk score from findings.

        Scoring: CRITICAL=25, HIGH=15, MEDIUM=5, LOW=1. Capped at 100.
        """
        score = sum(
            CoordinatorAgent._RISK_POINTS.get(f.severity, 0)
            for f in findings
        )
        return min(100, score)

    @staticmethod
    def risk_level(score: int) -> str:
        if score <= 20:
            return "LOW"
        if score <= 50:
            return "MEDIUM"
        if score <= 80:
            return "HIGH"
        return "CRITICAL"

    @staticmethod
    def risk_emoji(score: int) -> str:
        if score <= 20:
            return "✅"
        if score <= 50:
            return "⚠️"
        if score <= 80:
            return "🔴"
        return "🚨"

    # Backward-compatible aliases for internal use
    _risk_level = risk_level
    _risk_emoji = risk_emoji

    @staticmethod
    def _risk_score_block(findings: list[ReviewFinding], score: int) -> str:
        """Build the risk score summary block."""
        level = CoordinatorAgent.risk_level(score)
        emoji = CoordinatorAgent.risk_emoji(score)
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        parts = []
        for sev in ("critical", "high", "medium", "low"):
            if sev in counts:
                parts.append(f"{counts[sev]} {sev.capitalize()}")
        breakdown = " · ".join(parts)
        return f"{emoji} Risk Score: {level} ({score}/100)\n\n{breakdown}"

    #: Below a handful of findings there is nothing worth a model round-trip.
    _CONSOLIDATE_MIN_FINDINGS = 4

    #: Merging is cosmetic next to the review itself: a slow provider must not
    #: add minutes to a run that already finished.
    _CONSOLIDATE_TIMEOUT = 60.0

    async def _consolidate(self, findings: list[ReviewFinding]) -> tuple[list[ReviewFinding], int]:
        """Merge findings that different agents wrote up as the same defect.

        Returns the surviving findings and how many duplicates were absorbed.
        Any answer that does not parse cleanly leaves the list untouched: a
        missed merge is noise, a bad merge is a defect the customer never sees.
        """
        # Free and always safe: same-or-adjacent line is one defect.
        collapsed = self._collapse_same_line(findings)
        absorbed = len(findings) - len(collapsed)
        if len(collapsed) < self._CONSOLIDATE_MIN_FINDINGS:
            return collapsed, absorbed

        # Titles and lines carry the signal; full descriptions triple the
        # prompt and the model answered too slowly to be useful.
        listing = "\n".join(
            f"{i}. [L{f.line_number or '?'}, {f.agent}] {f.title} — {(f.description or '')[:110]}"
            for i, f in enumerate(collapsed)
        )
        prompt = (
            "Reviewers below reported on the same code. Several describe the SAME defect "
            "in different words; others are genuinely different problems that happen to sit "
            "on nearby lines.\n\n"
            f"{listing}\n\n"
            "Group the entries that describe one and the same defect. Rules:\n"
            "- Two entries belong together only if fixing one fixes the other.\n"
            "- Never group different kinds of problems (a SQL injection and a missing "
            "authentication check are different, even on the same line).\n"
            "- Every index appears exactly once.\n\n"
            "Reply with JSON only: {\"groups\": [[0, 3], [1], [2, 5]]}"
        )

        groups = await self._request_groups(prompt, len(collapsed))
        if not groups:
            return collapsed, absorbed

        out: list[ReviewFinding] = []
        for group in groups:
            members = [collapsed[i] for i in group]
            if len(members) == 1:
                out.append(members[0])
                continue
            primary = min(
                members, key=lambda f: (SEVERITY_ORDER.get(f.severity.value, 99), f.line_number or 0)
            )
            agents = [primary.agent]
            for member in members:
                for name in (member.agent or "").split(" + "):
                    if name and name not in agents:
                        agents.append(name)
            extra = [
                f"_{m.agent}:_ {m.description}"
                for m in members
                if m is not primary and m.description and m.description not in (primary.description or "")
            ]
            description = primary.description or ""
            if extra:
                description = "\n\n".join([description, *extra]) if description else "\n\n".join(extra)
            out.append(
                ReviewFinding(
                    agent=" + ".join(agents),
                    severity=primary.severity,
                    title=primary.title,
                    description=description,
                    file_path=primary.file_path,
                    line_number=min(f.line_number or 0 for f in members) or primary.line_number,
                    code_snippet=primary.code_snippet,
                    suggestion=primary.suggestion
                    or next((f.suggestion for f in members if f.suggestion), None),
                )
            )

        absorbed += len(collapsed) - len(out)
        if absorbed:
            logger.info("Consolidated %d duplicate findings into %d", absorbed, len(out))
        return out, absorbed

    @staticmethod
    def _collapse_same_line(findings: list[ReviewFinding]) -> list[ReviewFinding]:
        """Merge findings that share a line or sit one line apart."""
        kept: list[ReviewFinding] = []
        for finding in findings:
            target = None
            for idx, existing in enumerate(kept):
                if (
                    finding.file_path
                    and finding.file_path == existing.file_path
                    and finding.line_number
                    and existing.line_number
                    and abs(finding.line_number - existing.line_number) <= 1
                ):
                    target = idx
                    break
            if target is None:
                kept.append(finding)
                continue
            current = kept[target]
            primary = (
                finding
                if SEVERITY_ORDER.get(finding.severity.value, 99)
                < SEVERITY_ORDER.get(current.severity.value, 99)
                else current
            )
            other = current if primary is finding else finding
            agents = [primary.agent]
            for name in (other.agent or "").split(" + "):
                if name and name not in agents:
                    agents.append(name)
            description = primary.description or ""
            if other.description and other.description not in description:
                description = f"{description}\n\n_{other.agent}:_ {other.description}".strip()
            kept[target] = ReviewFinding(
                agent=" + ".join(agents),
                severity=primary.severity,
                title=primary.title,
                description=description,
                file_path=primary.file_path,
                line_number=min(finding.line_number or 0, current.line_number or 0)
                or primary.line_number,
                code_snippet=primary.code_snippet or other.code_snippet,
                suggestion=primary.suggestion or other.suggestion,
            )
        return kept

    async def _request_groups(self, prompt: str, count: int) -> list[list[int]] | None:
        """Ask the model for a partition, retrying once.

        Runs after every agent has finished, when a throttled key is usually
        recovering, so a single retry is worth the wall clock; the second
        failure is treated as "no answer" rather than blocking the report.
        """
        from langchain_core.messages import HumanMessage

        for attempt in range(2):
            try:
                response = await asyncio.wait_for(
                    self.llm.ainvoke([HumanMessage(content=prompt)]),
                    timeout=self._CONSOLIDATE_TIMEOUT,
                )
            except Exception as exc:
                logger.warning(
                    "Consolidation attempt %d/2 failed: %s: %s",
                    attempt + 1,
                    type(exc).__name__,
                    exc,
                )
                if attempt == 0:
                    await asyncio.sleep(10)
                continue
            groups = self._parse_groups(getattr(response, "content", ""), count)
            if groups:
                return groups
            logger.warning("Consolidation returned an unusable grouping: %r", getattr(response, "content", "")[:120])
            return None
        return None

    @staticmethod
    def _parse_groups(content: str, count: int) -> list[list[int]] | None:
        """Parse ``{"groups": [[0, 3], [1]]}`` into a complete, valid partition."""
        import json

        text = content or ""
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            return None

        raw = payload.get("groups") if isinstance(payload, dict) else None
        if not isinstance(raw, list) or not raw:
            return None

        seen: set[int] = set()
        groups: list[list[int]] = []
        for group in raw:
            if not isinstance(group, list) or not group:
                return None
            clean: list[int] = []
            for idx in group:
                if not isinstance(idx, int) or isinstance(idx, bool):
                    return None
                if idx < 0 or idx >= count or idx in seen:
                    return None
                seen.add(idx)
                clean.append(idx)
            groups.append(clean)

        # A partition that drops findings would silently hide defects.
        if len(seen) != count:
            return None
        return groups

    async def _resolve_conflicts(
        self, findings: list[ReviewFinding]
    ) -> tuple[list[ReviewFinding], int]:
        """Detect and resolve conflicting findings via LLM.

        Finds groups of findings about the same issue but with different
        severities, sends them to the LLM for resolution, and returns
        the merged results.

        Returns:
            (resolved_findings, number_of_conflicts_resolved)
        """
        if len(findings) < 2:
            return findings, 0

        # Group findings by semantic similarity
        groups: list[list[int]] = []
        assigned: set[int] = set()

        for i, f in enumerate(findings):
            if i in assigned:
                continue
            group = [i]
            assigned.add(i)
            kw_i = _extract_keywords(f.title + " " + f.description)

            for j in range(i + 1, len(findings)):
                if j in assigned:
                    continue
                kw_j = _extract_keywords(findings[j].title + " " + findings[j].description)
                if _jaccard_similarity(kw_i, kw_j) >= 0.5:
                    group.append(j)
                    assigned.add(j)
                    kw_i = kw_i | kw_j  # expand keywords for group

            groups.append(group)

        # Find groups with conflicting severities
        conflict_groups = []
        for group in groups:
            if len(group) < 2:
                continue
            severities = {findings[i].severity for i in group}
            if len(severities) > 1:
                conflict_groups.append(group)

        if not conflict_groups:
            return findings, 0

        # Try LLM resolution
        resolved = list(findings)
        conflicts_resolved = 0

        for group in conflict_groups:
            try:
                conflict_desc = []
                for idx in group:
                    f = findings[idx]
                    conflict_desc.append(
                        f"[{f.severity.value.upper()}] {f.title} ({f.agent}): {f.description}"
                    )

                prompt = (
                    "The following findings describe the same code issue but with different severity levels. "
                    "Determine the correct severity and provide a merged description.\n\n"
                    "Conflicting findings:\n" + "\n".join(conflict_desc) + "\n\n"
                    "Respond with exactly one line in this format:\n"
                    "SEVERITY: <CRITICAL|HIGH|MEDIUM|LOW>\n"
                    "TITLE: <merged title>\n"
                    "DESCRIPTION: <merged description>"
                )

                from langchain_core.messages import HumanMessage
                response = await self.llm.ainvoke([HumanMessage(content=prompt)])
                parsed = self._parse_conflict_response(response.content)

                if parsed:
                    # Replace all findings in the group with the resolved one
                    best_idx = min(group, key=lambda i: SEVERITY_ORDER.get(findings[i].severity.value, 99))
                    resolved[group[0]] = ReviewFinding(
                        agent=findings[best_idx].agent,
                        severity=parsed["severity"],
                        title=parsed["title"],
                        description=parsed["description"],
                        line_number=findings[best_idx].line_number,
                        code_snippet=findings[best_idx].code_snippet,
                        suggestion=findings[best_idx].suggestion,
                    )
                    # Mark others for removal
                    for idx in group[1:]:
                        resolved[idx] = None  # type: ignore
                    conflicts_resolved += 1

            except Exception as exc:
                logger.warning("Conflict resolution failed, keeping highest severity: %s", exc)
                # Fallback: keep highest severity
                best_idx = min(group, key=lambda i: SEVERITY_ORDER.get(findings[i].severity.value, 99))
                for idx in group:
                    if idx != best_idx:
                        resolved[idx] = None  # type: ignore
                conflicts_resolved += 1

        # Remove None entries
        resolved = [f for f in resolved if f is not None]
        resolved.sort(key=lambda f: SEVERITY_ORDER.get(f.severity.value, 99))

        return resolved, conflicts_resolved

    def _parse_conflict_response(self, response: str) -> dict | None:
        """Parse LLM conflict resolution response."""
        sev_match = re.search(r"SEVERITY:\s*(CRITICAL|HIGH|MEDIUM|LOW)", response, re.IGNORECASE)
        title_match = re.search(r"TITLE:\s*(.+)", response)
        desc_match = re.search(r"DESCRIPTION:\s*(.+)", response, re.DOTALL)

        if not sev_match:
            return None

        try:
            severity = Severity(sev_match.group(1).lower())
        except ValueError:
            return None

        return {
            "severity": severity,
            "title": title_match.group(1).strip() if title_match else "Conflict resolved",
            "description": desc_match.group(1).strip() if desc_match else "",
        }

    def _generate_summary(
        self,
        findings: list[ReviewFinding],
        agent_results: list[AgentResult],
        risk_score: int = 0,
        conflicts_resolved: int = 0,
    ) -> str:
        """Build a human-readable summary with risk score and severity breakdown."""
        # The risk block above already prints "3 Critical · 5 High · …", so the
        # counts belong in the sentence, not on a second line as a raw dict
        # ("critical: 3, high: 5") which read like leftover debug output.
        severity_counts: dict[str, int] = {}
        for f in findings:
            severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1
        tally = ", ".join(
            f"{severity_counts[sev]} {sev}"
            for sev in ("critical", "high", "medium", "low")
            if severity_counts.get(sev)
        )

        # A failed agent contributes nothing, so counting it as a reviewer
        # overstates coverage; say how many really ran and flag the gap.
        completed = [r for r in agent_results if not r.failed]
        incomplete = len(agent_results) - len(completed)

        lines = [
            self._risk_score_block(findings, risk_score),
            "",
            f"Review completed with {len(findings)} findings across "
            f"{len(completed)} agents" + (f" ({tally})" if tally else "") + ".",
        ]

        if incomplete:
            lines.append(
                f"\n⚠️ {incomplete} of {len(agent_results)} agents did not finish — "
                "the LLM provider kept throttling until the review budget ran out. "
                "This report covers what completed; re-run for full coverage."
            )

        if conflicts_resolved > 0:
            lines.append(f"\n✅ AI analysis resolved {conflicts_resolved} conflicting assessment(s).")

        critical_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        if critical_high:
            lines.append(f"\n⚠️ {len(critical_high)} critical/high severity issues require immediate attention:")
            for f in critical_high[:5]:
                lines.append(f"  - [{f.severity.value.upper()}] {f.title} ({f.agent})")

        return "\n".join(lines)
