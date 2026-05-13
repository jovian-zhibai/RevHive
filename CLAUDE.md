# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RevHive is a Python CLI tool (PyPI: `revhive-ai`, import: `revhive`) that runs 10 AI agents in parallel to perform code review: 9 reviewers (Style, Security, Performance, Logic, Repo, Refactor, Fix, Test, Doc) and a Coordinator that deduplicates, resolves conflicts, and generates a risk-scored report. Workflow orchestration uses LangGraph StateGraph.

## Common Commands

```bash
# Install (dev)
pip install -e ".[dev]"

# Install (all optional deps including anthropic, tree-sitter)
pip install -e ".[all]"

# Run tests
pytest -v

# Run a single test file
pytest tests/test_agents.py -v

# Lint
ruff check src/ tests/

# Run review (requires LLM_API_KEY)
revhive review --file path/to/file.py
revhive review --diff HEAD~1

# Demo mode (no API key needed)
revhive demo
```

## Architecture

### Core Pipeline (LangGraph StateGraph)

`graph/workflow.py` defines `CodeReviewWorkflow` which builds a StateGraph where all 9 agent nodes run in parallel fan-out, then converge into a single `coordinate` node. The graph: `START -> [9 parallel agent nodes] -> coordinate -> END`.

### Agent System

All agents inherit from `BaseReviewAgent` (`agents/base.py`), an ABC requiring implementation of `get_system_prompt()` and `get_review_focus()`. Each agent returns an `AgentResult` containing `ReviewFinding` objects. Agents attempt structured output (Pydantic `with_structured_output`) first, falling back to regex parsing of bullet-point LLM output.

To add a new agent: create the file in `agents/`, extend `BaseReviewAgent`, register it in `agents/__init__.py`, and add it to `_AGENT_CLASSES` in `graph/workflow.py`.

### Coordinator (`agents/coordinator.py`)

Performs three steps after agents finish: (1) semantic dedup via Jaccard similarity (threshold 0.7), (2) LLM-based conflict resolution when agents disagree on severity, (3) risk score calculation (CRITICAL=25, HIGH=15, MEDIUM=5, LOW=1, capped at 100).

### Key Modules

- `config.py` — loads `.revhive.yml`, defines `GuardianConfig` and LLM model presets
- `demo.py` — mock workflow that runs without an API key (deterministic findings)
- `utils/llm_client.py` — factory returning `ChatOpenAI` or `ChatAnthropic` with provider presets
- `utils/parser.py` — code parsing via tree-sitter (optional) with regex fallback, 17 languages
- `utils/dedup.py` — semantic deduplication logic
- `models/schemas.py` — Pydantic schemas for structured LLM output
- `server/` — separate FastAPI GitHub App webhook receiver (not part of pip package)

### Supported LLM Providers

MiMo (default), OpenAI, DeepSeek, Qwen, GLM, Kimi, Anthropic. Set via `LLM_MODEL` env var (preset name or full model ID) and `LLM_BASE_URL`. Anthropic requires `pip install -e ".[anthropic]"`.

## Configuration

- LLM credentials: `LLM_API_KEY` (required), `LLM_BASE_URL`, `LLM_MODEL` env vars
- Review config: `.revhive.yml` in project root (agent selection, ignore patterns)
- Ruff config: in `pyproject.toml` under `[tool.ruff]` (line-length=120, target py310)
- pytest config: in `pyproject.toml` under `[tool.pytest.ini_options]`

## CI

GitHub Actions (`.github/workflows/ci.yml`): runs pytest + ruff across Python 3.10/3.11/3.12, pip-audit + bandit for security, and a demo consistency check that verifies risk score/severity counts match documentation.
