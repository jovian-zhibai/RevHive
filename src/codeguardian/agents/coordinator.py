"""Coordinator agent that orchestrates the review process and synthesizes results."""


from codeguardian.agents.base import BaseReviewAgent, AgentResult, ReviewFinding, Severity


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

        deduplicated = self._deduplicate_findings(all_findings)

        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
        }
        deduplicated.sort(key=lambda f: severity_order.get(f.severity, 99))

        summary = self._generate_summary(deduplicated, results)

        return AgentResult(
            agent_name="CoordinatorAgent",
            findings=deduplicated,
            summary=summary,
            token_usage=sum(r.token_usage for r in results),
        )

    def _deduplicate_findings(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        """Remove duplicate or near-duplicate findings."""
        seen_titles = set()
        unique = []
        for f in findings:
            normalized = f.title.lower().strip()
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique.append(f)
        return unique

    def _generate_summary(self, findings: list[ReviewFinding], agent_results: list[AgentResult]) -> str:
        """Generate an overall summary of the review."""
        severity_counts = {}
        for f in findings:
            severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

        lines = [
            f"Review completed with {len(findings)} findings across {len(agent_results)} agents.",
            "Severity breakdown: " + ", ".join(f"{k}: {v}" for k, v in sorted(severity_counts.items())),
        ]

        critical_high = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        if critical_high:
            lines.append(f"\n⚠️ {len(critical_high)} critical/high severity issues require immediate attention:")
            for f in critical_high[:5]:
                lines.append(f"  - [{f.severity.value.upper()}] {f.title} ({f.agent})")

        return "\n".join(lines)
