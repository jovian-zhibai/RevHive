"""Base agent class for all review agents."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

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
    ):
        self.name = name
        self.description = description

        kwargs = {}
        if model:
            kwargs["model"] = model
        kwargs["max_retries"] = max_retries
        kwargs["request_timeout"] = request_timeout

        # Pass credentials to ChatOpenAI explicitly so it doesn't fail
        # when env vars are absent (e.g., CI or demo mode).
        if not api_key:
            raise ValueError(
                "API key is required. Set the LLM_API_KEY environment variable "
                "or pass api_key to the agent constructor."
            )
        kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        self.llm = ChatOpenAI(
            temperature=0.1,
            **kwargs,
        )

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
            logger.error("%s: LLM call failed for %s: %s", self.name, file_path, exc)
            return AgentResult(
                agent_name=self.name,
                summary=f"Review failed: {exc}",
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

    def _parse_findings(self, response: str) -> list[ReviewFinding]:
        """Parse LLM response into structured findings."""
        findings = []
        current = {}

        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("- Severity:"):
                if current:
                    findings.append(self._dict_to_finding(current))
                current = {"agent": self.name}
                sev = line.split(":", 1)[1].strip().strip("[]")
                try:
                    current["severity"] = Severity(sev.lower())
                except ValueError:
                    current["severity"] = Severity.LOW
            elif line.startswith("- Title:"):
                current["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Line:"):
                try:
                    current["line_number"] = int("".join(filter(str.isdigit, line.split(":", 1)[1])))
                except (ValueError, IndexError):
                    pass
            elif line.startswith("- Description:"):
                current["description"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Suggestion:"):
                current["suggestion"] = line.split(":", 1)[1].strip()

        if current:
            findings.append(self._dict_to_finding(current))

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
        """Extract the summary section from the response."""
        lines = response.split("\n")
        capture = False
        summary_lines = []
        for line in lines:
            if "summary" in line.lower() and ":" in line:
                capture = True
                continue
            if capture:
                if line.strip().startswith("-") and "Severity" in line:
                    break
                summary_lines.append(line)
        return "\n".join(summary_lines).strip() if summary_lines else "Review completed."
