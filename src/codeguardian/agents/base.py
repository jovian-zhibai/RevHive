"""Base agent class for all review agents."""

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

try:
    from langchain_anthropic import ChatAnthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Ordered severity levels for review findings."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ReviewFinding:
    """A single review finding from an agent."""
    agent: str
    severity: Severity
    title: str
    description: str
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None
    suggestion: Optional[str] = None


@dataclass
class AgentResult:
    """Result from a single agent's review."""
    agent_name: str
    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    token_usage: int = 0


class BaseReviewAgent(ABC):
    """Base class for all specialized review agents."""

    def __init__(
        self,
        name: str,
        description: str,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        request_timeout: int = 120,
        provider: Optional[str] = None,
    ):
        self.name = name
        self.description = description

        # Resolve model presets (e.g. "mimo" -> base_url + model name).
        from codeguardian.config import GuardianConfig
        _preset = GuardianConfig().resolve_preset(model)
        if _preset:
            base_url = base_url or _preset.get("base_url")
            model = _preset.get("model", model)
            provider = provider or _preset.get("provider")

        kwargs = {}
        if model:
            kwargs["model"] = model
        kwargs["max_retries"] = max_retries
        kwargs["request_timeout"] = request_timeout

        # Pass credentials explicitly so it doesn't fail when env vars are
        # absent (e.g., CI or demo mode).
        if not api_key:
            raise ValueError(
                "API key is required. Set the LLM_API_KEY environment variable "
                "or pass api_key to the agent constructor."
            )

        # Choose LLM client based on provider.
        if provider == "anthropic":
            if not HAS_ANTHROPIC:
                raise ImportError(
                    "langchain-anthropic is required for Anthropic models. "
                    "Install it with: pip install langchain-anthropic"
                )
            kwargs["api_key"] = api_key
            self.llm = ChatAnthropic(temperature=0.1, **kwargs)
        else:
            kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            self.llm = ChatOpenAI(temperature=0.1, **kwargs)

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
        messages = [
            SystemMessage(content=self.get_system_prompt()),
            HumanMessage(content=self._build_human_prompt(code, file_path)),
        ]

        try:
            response = await self.llm.ainvoke(messages)
        except Exception as exc:
            safe_msg = str(exc)
            api_key = os.getenv("LLM_API_KEY", "")
            if api_key:
                safe_msg = safe_msg.replace(api_key, "***")
            logger.error("%s: LLM call failed for %s: %s", self.name, file_path, safe_msg)
            return AgentResult(
                agent_name=self.name,
                summary=f"Review failed: {safe_msg}",
            )

        findings = self._parse_findings(response.content)
        return AgentResult(
            agent_name=self.name,
            findings=findings,
            summary=self._extract_summary(response.content),
            token_usage=response.response_metadata.get("token_usage", {}).get("total_tokens", 0),
        )

    def _build_human_prompt(self, code: str, file_path: str) -> str:
        """Build the user-facing prompt with code context and expected output format."""
        return f"""Please review the following code file `{file_path}`:

```
{code}
```

Focus on: {self.get_review_focus()}

Output format for each finding:
- Severity: [LOW/MEDIUM/HIGH/CRITICAL]
- Title: [Brief title]
- Line: [Line number if applicable]
- Description: [What's wrong]
- Suggestion: [How to fix]

End with a brief summary of your review."""

    # Regex patterns for finding block detection
    _SEV_RE = re.compile(r"^- Severity:\s*(.+)", re.IGNORECASE | re.MULTILINE)
    _SEV_ALT_RE = re.compile(r"\[?(LOW|MEDIUM|HIGH|CRITICAL)\]?", re.IGNORECASE)
    _TITLE_RE = re.compile(r"^- Title:\s*(.+)", re.IGNORECASE)
    _LINE_RE = re.compile(r"^- Line:\s*(.+)", re.IGNORECASE)
    _DESC_RE = re.compile(r"^- Description:\s*(.+)", re.IGNORECASE)
    _SUGG_RE = re.compile(r"^- Suggestion:\s*(.+)", re.IGNORECASE)
    _FIELD_RE = re.compile(r"^-\s*(Severity|Title|Line|Description|Suggestion):\s*", re.IGNORECASE)

    def _parse_findings(self, response: str) -> list[ReviewFinding]:
        """Parse LLM response into structured findings.

        Handles the primary bullet-point format::

            - Severity: HIGH
            - Title: SQL Injection
            - Line: 12
            - Description: User input interpolated into SQL query.
            - Suggestion: Use parameterized queries.

        Multi-line ``Description`` and ``Suggestion`` values are accumulated
        until the next ``- Field:`` marker.  If the response contains no
        parseable findings at all, the entire text is returned as a single
        finding so that review output is never silently lost.
        """
        findings: list[ReviewFinding] = []
        current: dict = {}

        for line in response.split("\n"):
            stripped = line.strip()

            # New finding starts at "- Severity:"
            if stripped.lower().startswith("- severity:"):
                if current:
                    findings.append(self._dict_to_finding(current))
                current = {"agent": self.name}
                sev = stripped.split(":", 1)[1].strip().strip("[]")
                try:
                    current["severity"] = Severity(sev.lower())
                except ValueError:
                    current["severity"] = Severity.LOW
                continue

            if not current:
                continue

            # Other fields
            if stripped.lower().startswith("- title:"):
                current["title"] = stripped.split(":", 1)[1].strip()
            elif stripped.lower().startswith("- line:"):
                raw = stripped.split(":", 1)[1].strip()
                digits = "".join(filter(str.isdigit, raw))
                if digits:
                    try:
                        current["line_number"] = int(digits)
                    except ValueError:
                        pass
            elif stripped.lower().startswith("- description:"):
                current["description"] = stripped.split(":", 1)[1].strip()
            elif stripped.lower().startswith("- suggestion:"):
                current["suggestion"] = stripped.split(":", 1)[1].strip()

            # Multi-line continuation: if the line does NOT start with a new
            # "- Field:" marker, append it to the current active field.
            elif not self._FIELD_RE.match(stripped) and stripped:
                # Determine which field was last set and append
                if "suggestion" in current and not stripped.startswith("-"):
                    current["suggestion"] += " " + stripped
                elif "description" in current and not stripped.startswith("-"):
                    current["description"] += " " + stripped

        if current:
            findings.append(self._dict_to_finding(current))

        # Fallback: if no structured findings were parsed, wrap the whole
        # response as a single finding so output is never silently lost.
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
            line_number=d.get("line_number"),
            suggestion=d.get("suggestion"),
        )

    def _extract_summary(self, response: str) -> str:
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
                    first_line = lines[i][header_match.end():].strip()
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
        findings = self._parse_findings(response)
        if findings:
            counts: dict[str, int] = {}
            for f in findings:
                counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
            parts = [f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: ["critical", "high", "medium", "low"].index(x[0]))]
            return f"Review completed with {len(findings)} findings ({', '.join(parts)})."

        return "Review completed."
