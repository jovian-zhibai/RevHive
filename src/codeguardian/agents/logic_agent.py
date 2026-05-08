"""Business logic review agent."""

from codeguardian.agents.base import BaseReviewAgent


class LogicAgent(BaseReviewAgent):
    """Reviews business logic for correctness and robustness."""

    def __init__(self, **kwargs):
        super().__init__(
            name="LogicAgent",
            description="Reviews business logic, edge cases, error handling, and type safety",
            **kwargs,
        )

    def get_system_prompt(self) -> str:
        return """You are a senior software engineer focused on code correctness. Identify:
1. **Edge Cases** — Unhandled null/None, empty collections, boundary conditions, off-by-one errors
2. **Error Handling** — Missing try/except, swallowed exceptions, overly broad exception catching
3. **Type Safety** — Implicit type coercion, missing type checks, unsafe casts
4. **Race Conditions** — Shared mutable state, TOCTOU issues, missing synchronization
5. **Logic Errors** — Wrong boolean conditions, incorrect loop termination, swapped variables
6. **Resource Management** — Unclosed connections, missing finally blocks, resource leaks

Focus on bugs that would cause incorrect behavior or runtime failures in production."""

    def get_review_focus(self) -> str:
        return "edge cases, error handling, type safety, race conditions, logic errors, resource management"
