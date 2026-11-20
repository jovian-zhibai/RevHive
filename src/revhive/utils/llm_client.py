"""Shared LLM client factory.

Consolidates model preset resolution, provider detection, and
ChatOpenAI / ChatAnthropic instantiation used by both
``BaseReviewAgent`` and ``ConversationReviewer``.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI

try:
    from langchain_anthropic import ChatAnthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


def _mask_api_key(api_key: str) -> str:
    """Return a masked version of the API key for safe display.

    Shows only the first 4 characters followed by ``****``.
    Returns ``****`` if the key is shorter than 4 characters.
    """
    if not api_key:
        return "****"
    if len(api_key) <= 4:
        return "****"
    return api_key[:4] + "****"


def _is_local_endpoint(base_url: Optional[str]) -> bool:
    """True when *base_url* points at this machine (a self-hosted model server)."""
    if not base_url:
        return False
    host = (urlparse(base_url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local")


def create_llm_client(
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    temperature: float = 0.1,
    max_retries: int = 3,
    request_timeout: int = 120,
):
    """Create and return a LangChain chat model instance.

    Handles preset resolution (e.g. ``"mimo"`` → base_url + model),
    provider-based client selection (OpenAI-compatible vs Anthropic),
    and credential validation.

    Raises:
        ValueError: If *api_key* is empty and *base_url* is not local.
        ImportError: If *provider* is ``"anthropic"`` but ``langchain-anthropic``
            is not installed.
    """
    # Resolve model presets (e.g. "mimo" -> base_url + model name) before
    # validating the key: whether a key is needed depends on where the preset
    # points. A local inference server accepts any credential, including none.
    from revhive.config import MODEL_PRESETS
    _preset = MODEL_PRESETS.get(model, {}) if model else {}
    if _preset:
        base_url = base_url or _preset.get("base_url")
        model = _preset.get("model", model)
        provider = provider or _preset.get("provider")

    if not api_key:
        if _is_local_endpoint(base_url):
            # Ollama and friends take no credential. The OpenAI client still
            # rejects an empty string, so pass a placeholder that is never sent
            # anywhere but localhost.
            api_key = "local-model"
        else:
            raise ValueError(
                "API key is required. Set the LLM_API_KEY environment variable "
                "or pass api_key to the constructor. A local server "
                "(LLM_BASE_URL=http://127.0.0.1:11434/v1) needs no key."
            )

    kwargs: dict = {
        "api_key": api_key,
        "max_retries": max_retries,
        "request_timeout": request_timeout,
    }
    if model:
        kwargs["model"] = model

    if provider == "anthropic":
        if not HAS_ANTHROPIC:
            raise ImportError(
                "langchain-anthropic is required for Anthropic models. "
                "Install it with: pip install langchain-anthropic"
            )
        return ChatAnthropic(temperature=temperature, **kwargs)

    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(temperature=temperature, **kwargs)
