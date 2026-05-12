"""Pydantic schemas for structured LLM output."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class Finding(BaseModel):
    """A single review finding returned by an LLM."""

    model_config = {"extra": "ignore"}

    title: str = Field(description="Brief title of the finding")
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(
        description="Severity level"
    )
    description: str = Field(description="What's wrong")
    file_path: Optional[str] = Field(default=None, description="File path if applicable")
    line_number: Optional[int] = Field(default=None, description="Line number if applicable")
    code_snippet: Optional[str] = Field(default=None, description="Relevant code snippet")
    suggestion: str = Field(default="", description="How to fix")

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: dict) -> dict:
        """Allow title-case keys from LLMs that don't return lowercase."""
        if isinstance(data, dict):
            return {k.lower(): v for k, v in data.items()}
        return data


class ReviewResult(BaseModel):
    """Structured output from a code review agent."""

    model_config = {"extra": "ignore"}

    findings: list[Finding] = Field(default_factory=list, description="List of findings")
    summary: str = Field(default="", description="Brief summary of the review")
    risk_level: str = Field(default="LOW", description="Overall risk level: LOW/MEDIUM/HIGH/CRITICAL")

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: dict) -> dict:
        """Allow title-case keys from LLMs that don't return lowercase."""
        if isinstance(data, dict):
            return {k.lower(): v for k, v in data.items()}
        return data
