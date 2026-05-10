"""Security vulnerability scanning agent."""

from codeguardian.agents.base import BaseReviewAgent


class SecurityAgent(BaseReviewAgent):
    """Scans code for security vulnerabilities and risks."""

    def __init__(self, **kwargs):
        super().__init__(
            name="SecurityAgent",
            description="Scans for security vulnerabilities including injection, auth issues, and data exposure",
            **kwargs,
        )

    def get_system_prompt(self) -> str:
        return """You are a senior security engineer performing a code audit. Identify:
1. **Injection Vulnerabilities** — SQL injection, XSS, command injection, LDAP injection
2. **Authentication & Authorization** — Missing auth checks, privilege escalation, insecure session handling
3. **Data Exposure** — Hardcoded secrets/keys/passwords, sensitive data in logs, insecure error messages
4. **Cryptographic Issues** — Weak algorithms, missing encryption, improper key management
5. **Dependency Risks** — Known vulnerable packages, outdated dependencies
6. **Input Validation** — Missing sanitization, type coercion issues, path traversal

Treat every finding seriously. Provide the exact vulnerability type and a secure alternative implementation.

For each finding, output in this exact format:
- Severity: [LOW/MEDIUM/HIGH/CRITICAL]
- Title: [Brief title]
- Line: [Line number if applicable]
- Description: [What's wrong]
- Suggestion: [How to fix]

End with a brief summary of your review."""

    def get_review_focus(self) -> str:
        return "security vulnerabilities, injection risks, hardcoded secrets, auth issues, data exposure, input validation"
