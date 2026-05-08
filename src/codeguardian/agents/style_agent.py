"""Code style and conventions review agent."""

from codeguardian.agents.base import BaseReviewAgent


class StyleAgent(BaseReviewAgent):
    """Reviews code for style, naming conventions, and documentation."""

    def __init__(self, **kwargs):
        super().__init__(
            name="StyleAgent",
            description="Reviews code style, naming conventions, and documentation completeness",
            **kwargs,
        )

    def get_system_prompt(self) -> str:
        return """You are an expert code style reviewer. Your job is to identify:
1. **Naming Conventions** — Variable/function/class names that don't follow language conventions
2. **Code Formatting** — Inconsistent indentation, line length, spacing
3. **Documentation** — Missing or incomplete docstrings, comments for complex logic
4. **Code Organization** — Functions that are too long, mixed responsibilities
5. **Dead Code** — Unused imports, unreachable code, commented-out code blocks

You are thorough but pragmatic. Flag issues that genuinely hurt readability and maintainability, not nitpicks. Always provide a concrete suggestion for improvement."""

    def get_review_focus(self) -> str:
        return "code style, naming conventions, documentation, code organization, dead code"
