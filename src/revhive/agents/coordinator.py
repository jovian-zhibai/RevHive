"""Coordinator agent that orchestrates the review process and synthesizes results."""

import logging
import re

from revhive.agents.base import BaseReviewAgent, AgentResult, ReviewFinding, Severity, SEVERITY_ORDER
from revhive.utils.dedup import deduplicate_and_sort, _extract_keywords, _jaccard_similarity

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

        risk_score = self._calculate_risk_score(resolved)
        summary = self._generate_summary(resolved, results, risk_score, conflicts_resolved)

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
    def _risk_level(score: int) -> str:
        if score <= 20:
            return "LOW"
        if score <= 50:
            return "MEDIUM"
        if score <= 80:
            return "HIGH"
        return "CRITICAL"

    @staticmethod
    def _risk_emoji(score: int) -> str:
        if score <= 20:
            return "✅"
        if score <= 50:
            return "⚠️"
        if score <= 80:
            return "🔴"
        return "🚨"

    @staticmethod
    def _risk_score_block(findings: list[ReviewFinding], score: int) -> str:
        """Build the risk score summary block."""
        level = CoordinatorAgent._risk_level(score)
        emoji = CoordinatorAgent._risk_emoji(score)
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        parts = []
        for sev in ("critical", "high", "medium", "low"):
            if sev in counts:
                parts.append(f"{counts[sev]} {sev.capitalize()}")
        breakdown = " · ".join(parts)
        return f"{emoji} Risk Score: {level} ({score}/100)\n\n{breakdown}"

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
        severity_counts = {}
        for f in findings:
            severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

        lines = [
            self._risk_score_block(findings, risk_score),
            "",
            f"Review completed with {len(findings)} findings across {len(agent_results)} agents.",
            "Severity breakdown: " + ", ".join(f"{k}: {v}" for k, v in sorted(severity_counts.items())),
        ]

        if conflicts_resolved > 0:
            lines.append(f"\n✅ AI analysis resolved {conflicts_resolved} conflicting assessment(s).")

        critical_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        if critical_high:
            lines.append(f"\n⚠️ {len(critical_high)} critical/high severity issues require immediate attention:")
            for f in critical_high[:5]:
                lines.append(f"  - [{f.severity.value.upper()}] {f.title} ({f.agent})")

        return "\n".join(lines)
