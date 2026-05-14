"""Tests for LLM client factory."""

import os
import pytest
from unittest.mock import patch

from revhive.utils.llm_client import create_llm_client


# Clear proxy env vars for all tests to avoid SOCKS proxy issues
@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)


class TestCreateLlmClient:
    def test_empty_api_key_raises(self):
        with pytest.raises(ValueError, match="API key is required"):
            create_llm_client(api_key="")

    def test_none_api_key_raises(self):
        with pytest.raises(ValueError, match="API key is required"):
            create_llm_client(api_key=None)

    def test_returns_chat_openai_by_default(self):
        client = create_llm_client(api_key="test-key")
        from langchain_openai import ChatOpenAI
        assert isinstance(client, ChatOpenAI)

    def test_custom_model(self):
        client = create_llm_client(api_key="test-key", model="gpt-4o")
        assert client.model_name == "gpt-4o"

    def test_preset_resolution_mimo(self):
        client = create_llm_client(api_key="test-key", model="mimo")
        from langchain_openai import ChatOpenAI
        assert isinstance(client, ChatOpenAI)
        assert client.openai_api_base is not None

    def test_preset_resolution_deepseek(self):
        client = create_llm_client(api_key="test-key", model="deepseek")
        from langchain_openai import ChatOpenAI
        assert isinstance(client, ChatOpenAI)

    def test_unknown_model_still_works(self):
        client = create_llm_client(api_key="test-key", model="unknown-model-xyz")
        from langchain_openai import ChatOpenAI
        assert isinstance(client, ChatOpenAI)

    def test_anthropic_provider_without_package_raises(self):
        with patch("revhive.utils.llm_client.HAS_ANTHROPIC", False):
            with pytest.raises(ImportError, match="langchain-anthropic"):
                create_llm_client(api_key="test-key", provider="anthropic")

    def test_temperature_setting(self):
        client = create_llm_client(api_key="test-key", temperature=0.5)
        assert client.temperature == 0.5

    def test_max_retries_setting(self):
        client = create_llm_client(api_key="test-key", max_retries=5)
        assert client.max_retries == 5

    def test_request_timeout_setting(self):
        client = create_llm_client(api_key="test-key", request_timeout=60)
        assert client.request_timeout == 60

    def test_base_url_preserved_when_no_preset(self):
        client = create_llm_client(api_key="test-key", base_url="https://custom.api.com/v1", model="custom-model")
        assert str(client.openai_api_base) == "https://custom.api.com/v1" or client.openai_api_base == "https://custom.api.com/v1"
