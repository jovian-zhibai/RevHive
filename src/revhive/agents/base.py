"""Base agent class for all review agents."""

import contextlib
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


def _estimate_token_count(text: str) -> int:
    """Estimate token count from text length.

    Heuristic:
    - Chinese characters: ~2 tokens each
    - English words: ~1.3 tokens each (rounded up)
    - Mixed: count CJK chars separately, then split remaining by whitespace.
    """
    if not text:
        return 0
    cjk_count = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    # Remove CJK chars, count remaining words
    remaining = re.sub(r'[\u4e00-\u9fff]', ' ', text)
    word_count = len(remaining.split())
    return int(cjk_count * 2 + word_count * 1.3)


_RATE_LIMIT_MARKERS = (
    "429", "rate limit", "ratelimit", "rpm", "tpm", "quota",
    "too many requests", "resource_exhausted", "exceeded",
)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a provider rate-limit / quota error."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _RATE_LIMIT_MARKERS)


class Severity(Enum):
    """Ordered severity levels for review findings."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


@dataclass
class ReviewFinding:
    """A single review finding from an agent."""
    agent: str
    severity: Severity
    title: str
    description: str
    file_path: str | None = None
    line_number: int | None = None
    code_snippet: str | None = None
    suggestion: str | None = None


@dataclass
class AgentResult:
    """Result from a single agent's review."""
    agent_name: str
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    token_usage: int = 0
    risk_score: int | None = None
    # True when the agent never actually produced a review (LLM error, rate
    # limit, auth failure). Findings are meaningless in that case and callers
    # must not report "0 findings" as if the code were clean.
    failed: bool = False
    error: str = ""


class BaseReviewAgent(ABC):
    """Base class for all specialized review agents."""

    def __init__(
        self,
        name: str,
        description: str,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        # The client's own retries multiply latency: a hanging provider was
        # measured spending 120s per attempt and 8 minutes per agent before
        # the workflow's own backoff even saw the failure. Retrying is the
        # caller's job here, because it knows the budget and can share one
        # cooldown across agents.
        max_retries: int = 0,
        request_timeout: int = 90,
        provider: str | None = None,
    ):
        self.name = name
        self.description = description
        self._api_key = api_key or os.getenv("LLM_API_KEY", "")
        self._base_url = base_url
        self._model = model
        self._provider = provider
        self._max_retries = max_retries
        self._request_timeout = request_timeout
        self._use_structured_output = False

        self._build_client()

    def _build_client(self) -> None:
        """Create the LLM client (and structured-output wrapper) for the current model."""
        from revhive.utils.llm_client import create_llm_client

        self.llm = create_llm_client(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            provider=self._provider,
            max_retries=self._max_retries,
            request_timeout=self._request_timeout,
        )

        # Try to enable structured output
        try:
            from revhive.models.schemas import ReviewResult
            self._structured_llm = self.llm.with_structured_output(ReviewResult)
            self._use_structured_output = True
            logger.debug("%s: structured output enabled", self.name)
        except Exception:
            self._structured_llm = None
            logger.debug("%s: structured output not available, using regex fallback", self.name)

    def switch_model(self, model: str) -> None:
        """Rebuild the client against a different model on the same provider.

        Used when a provider rate-limits one model but still serves others
        (common on free tiers). Keeping the same base_url/api_key means the
        user's key and cost profile are unchanged.
        """
        if model == self._model:
            return
        self._model = model
        self._build_client()
        logger.info("%s: switched model to %s", self.name, model)

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        ...

    @abstractmethod
    def get_review_focus(self) -> str:
        """Return a brief description of what this agent focuses on."""
        ...

    async def review(self, code: str, file_path: str = "") -> AgentResult:
        """Run review on the given code, with automatic retry on failure."""
        system_prompt = self.get_system_prompt()
        human_prompt = self._build_human_prompt(code, file_path)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ]

        # Try structured output first
        if self._use_structured_output and self._structured_llm:
            try:
                result = await self._structured_llm.ainvoke(messages)
                findings = [
                    ReviewFinding(
                        agent=self.name,
                        severity=Severity(f.severity.lower()),
                        title=f.title,
                        description=f.description,
                        file_path=getattr(f, "file_path", None) or file_path,
                        line_number=f.line_number,
                        code_snippet=f.code_snippet,
                        suggestion=f.suggestion,
                    )
                    for f in result.findings
                ]
                # Diff reviews hand agents a "diff:<ref>" marker instead of a
                # path; recover the real file from the diff's hunk ranges
                # before anything downstream treats the marker as an anchor.
                if file_path.startswith("diff:"):
                    from revhive.utils.anchors import anchor_diff_findings
                    findings, _ = anchor_diff_findings(findings, code)
                # Estimate token usage from prompt + response text lengths
                prompt_text = system_prompt + human_prompt
                response_text = result.summary or ""
                for f in result.findings:
                    response_text += " " + f.title + " " + f.description
                estimated_tokens = _estimate_token_count(prompt_text) + _estimate_token_count(response_text)
                return AgentResult(
                    agent_name=self.name,
                    findings=findings,
                    summary=result.summary or self._auto_summary(findings),
                    token_usage=estimated_tokens,
                )
            except Exception as exc:
                # A rate-limited call must be retried, not downgraded: the
                # regex fallback loses structured severities and produces
                # misleadingly low risk scores. Let the workflow retry with
                # a long backoff instead.
                if _is_rate_limit_error(exc):
                    logger.warning(
                        "%s: rate-limited by provider for %s, retrying: %s",
                        self.name, file_path, exc,
                    )
                    raise
                logger.warning("%s: structured output failed for %s, falling back to regex: %s",
                              self.name, file_path, exc)

        # Fallback: raw LLM call + regex parsing
        try:
            response = await self.llm.ainvoke(messages)
        except Exception as exc:
            from revhive.utils.llm_client import _mask_api_key
            safe_msg = str(exc)
            if self._api_key:
                safe_msg = safe_msg.replace(self._api_key, _mask_api_key(self._api_key))
            logger.error("%s: LLM call failed for %s: %s", self.name, file_path, safe_msg)
            return AgentResult(
                agent_name=self.name,
                summary=f"Review failed: {safe_msg}",
                failed=True,
                error=safe_msg,
            )

        findings = self._parse_findings(response.content)
        # Fill in file_path from method parameter if LLM didn't provide it
        for f in findings:
            if not f.file_path and file_path:
                f.file_path = file_path
        # Same recovery as the structured path above: a "diff:<ref>" marker
        # is a review mode, not a file the user can open.
        if file_path.startswith("diff:"):
            from revhive.utils.anchors import anchor_diff_findings
            findings, _ = anchor_diff_findings(findings, code)
        # Use response metadata if available, otherwise estimate from text
        token_usage = response.response_metadata.get("token_usage", {}).get("total_tokens", 0)
        if not token_usage:
            prompt_text = system_prompt + human_prompt
            token_usage = _estimate_token_count(prompt_text) + _estimate_token_count(response.content)
        return AgentResult(
            agent_name=self.name,
            findings=findings,
            summary=self._extract_summary(response.content, findings),
            token_usage=token_usage,
        )

    def _build_human_prompt(self, code: str, file_path: str) -> str:
        """Build the user-facing prompt with code context and expected output format."""
        return f"""Please review the following code file `{file_path}`:

```
{code}
```

Focus on: {self.get_review_focus()}

Output format for each finding:
- Severity: [CRITICAL/HIGH/MEDIUM/LOW]
- Title: [Brief title]
- File: {file_path}
- Line: [Line number if applicable]
- Description: [What's wrong]
- Suggestion: [How to fix]

End with a brief summary of your review."""

    _FIELD_RE = re.compile(r"^[-*\d.]*\s*\**\s*(Severity|Title|Line|Description|Suggestion|File)\s*\**\s*:\s*", re.IGNORECASE)

    def _parse_findings(self, response: str) -> list[ReviewFinding]:
        """Parse LLM response into structured findings.

        Handles bullet-point format (with flexible markers) and JSON fallback.
        """
        findings: list[ReviewFinding] = []
        current: dict = {}

        for line in response.split("\n"):
            stripped = line.strip()

            # New finding starts at severity marker (flexible: -, *, 1., etc.)
            sev_match = re.match(r"^[-*\d.]*\s*\**\s*Severity\s*\**\s*:\s*(.*)", stripped, re.IGNORECASE)
            if sev_match:
                if current:
                    findings.append(self._dict_to_finding(current))
                current = {"agent": self.name}
                sev = sev_match.group(1).strip()
                # Models often answer "**CRITICAL**" or "CRITICAL (RCE)" —
                # strip markdown/decoration before matching, otherwise every
                # finding silently degrades to the default severity.
                sev_clean = re.sub(r"[^A-Za-z]", "", sev).lower()
                try:
                    current["severity"] = Severity(sev_clean)
                except ValueError:
                    # Unknown severity: assume MEDIUM, never LOW — silently
                    # under-rating a finding is worse than over-rating it.
                    current["severity"] = Severity.MEDIUM
                continue

            if not current:
                continue

            # Parse other fields with flexible matching
            field_match = self._FIELD_RE.match(stripped)
            if field_match:
                field_name = field_match.group(1).lower()
                value = stripped[field_match.end():].strip()
                match field_name:
                    case "title":
                        current["title"] = value
                    case "line":
                        digits = "".join(filter(str.isdigit, value))
                        if digits:
                            with contextlib.suppress(ValueError):
                                current["line_number"] = int(digits)
                    case "description":
                        current["description"] = value
                    case "suggestion":
                        current["suggestion"] = value
                    case "file":
                        current["file_path"] = value
            elif stripped:
                # Multi-line continuation
                if "suggestion" in current:
                    current["suggestion"] += " " + stripped
                elif "description" in current:
                    current["description"] += " " + stripped

        if current:
            findings.append(self._dict_to_finding(current))

        # JSON fallback: if regex found nothing, try parsing as JSON
        if not findings and response.strip():
            import json
            try:
                # Strip markdown code fences if present
                text = response.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                data = json.loads(text)
                items = data if isinstance(data, list) else data.get("findings", data.get("review_findings", []))
                for item in items:
                    if isinstance(item, dict):
                        findings.append(ReviewFinding(
                            agent=self.name,
                            severity=Severity(str(item.get("severity", "medium")).strip().lower()),
                            title=item.get("title", "Unknown"),
                            description=item.get("description", ""),
                            file_path=item.get("file", item.get("file_path")),
                            line_number=item.get("line", item.get("line_number")),
                            suggestion=item.get("suggestion"),
                        ))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

        # Last resort: wrap entire response as a single finding
        if not findings and response.strip():
            findings.append(ReviewFinding(
                agent=self.name,
                severity=Severity.LOW,
                title=response.strip()[:80],
                description=response.strip(),
            ))

        return findings

    def _dict_to_finding(self, d: dict) -> ReviewFinding:
        """Convert a parsed dict into a :class:`ReviewFinding`."""
        return ReviewFinding(
            agent=d.get("agent", self.name),
            severity=d.get("severity", Severity.LOW),
            title=d.get("title", "Unknown"),
            description=d.get("description", ""),
            file_path=d.get("file_path"),
            line_number=d.get("line_number"),
            suggestion=d.get("suggestion"),
        )

    def _extract_summary(self, response: str, findings: list[ReviewFinding] | None = None) -> str:
        """Extract or generate a summary from the LLM response.

        Strategy:
        1. Search from the end of the text for a line containing "summary"
           (case-insensitive) followed by ``:`` — capture everything after it.
        2. Also try Markdown-style headers (``## Summary``, ``### Summary``).
        3. If nothing found, auto-generate from the parsed findings count.
        """
        lines = response.split("\n")

        # Strategy 1: search from the end for "summary:" or "## summary"
        for i in range(len(lines) - 1, -1, -1):
            lower = lines[i].lower().strip()
            if ("summary" in lower or "总结" in lower) and (":" in lines[i] or lower.startswith("#")):
                # Grab the text after the colon/header marker
                colon_pos = lines[i].find(":")
                header_match = re.match(r"^#{1,4}\s*", lines[i])
                if colon_pos >= 0:
                    first_line = lines[i][colon_pos + 1:].strip()
                elif header_match:
                    first_line = re.sub(r"^#{1,4}\s*\w+\s*", "", lines[i]).strip()
                else:
                    first_line = ""

                summary_parts = [first_line] if first_line else []
                for j in range(i + 1, len(lines)):
                    nxt = lines[j].strip()
                    # Stop if we hit another finding block or end-of-content marker
                    if nxt.lower().startswith("- severity:") or nxt.startswith("---"):
                        break
                    if nxt:
                        summary_parts.append(nxt)
                if summary_parts:
                    return " ".join(summary_parts).strip()

        # Strategy 2: auto-generate from findings
        if findings is None:
            findings = self._parse_findings(response)
        if findings:
            counts: dict[str, int] = {}
            for f in findings:
                counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
            parts = [f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: SEVERITY_ORDER.get(x[0], 99))]
            return f"Review completed with {len(findings)} findings ({', '.join(parts)})."

        return "Review completed."

    def _auto_summary(self, findings: list[ReviewFinding]) -> str:
        """Auto-generate a summary from findings list."""
        if not findings:
            return "Review completed with no findings."
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: SEVERITY_ORDER.get(x[0], 99))]
        return f"Review completed with {len(findings)} findings ({', '.join(parts)})."
