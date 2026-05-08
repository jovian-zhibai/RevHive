# Contributing to CodeGuardian

We welcome contributions! CodeGuardian is an AI-powered multi-agent code review system compatible with MiMo, OpenAI, and DeepSeek.

## Getting Started

```bash
git clone https://github.com/YOUR_USERNAME/codeguardian.git
cd codeguardian
pip install -e ".[dev]"
python examples/sample_review.py  # Demo mode (no API key needed)
```

## Development Workflow

1. Fork the repo and create a branch
2. Make your changes
3. Run tests: `pytest -v`
4. Run linter: `ruff check src/ tests/`
5. Submit a PR

## Project Structure

```
src/codeguardian/
  agents/       # Specialized review agents
  graph/        # LangGraph workflow orchestration
  utils/        # Parsing utilities
  team/         # Batch processing engine
  demo.py       # Demo mode (no API key required)
```

## Adding a New Agent

1. Create a new file in `src/codeguardian/agents/`
2. Extend `BaseReviewAgent`
3. Implement `get_system_prompt()` and `get_review_focus()`
4. Register in `agents/__init__.py`
5. Add to the workflow in `graph/workflow.py`

## Running Against MiMo

```bash
export LLM_API_KEY=your-mimo-key
export LLM_BASE_URL=https://platform.xiaomimimo.com/api/v1
export LLM_MODEL=mimo-v2.5-pro
codeguardian review --file src/main.py
```
