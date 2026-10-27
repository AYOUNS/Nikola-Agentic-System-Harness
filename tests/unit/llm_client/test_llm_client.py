"""Unit tests for agent.llm.client.make_client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.llm.client import make_client

_VALID_CONFIG = {
    "llm": {
        "model": "qwen-agentworld-35b-a3b",
        "base_url": "https://llm-keys.innkube.fim.uni-passau.de/",
        "temperature": 0.7,
        "timeout_seconds": 60,
        "available_models": ["qwen-agentworld-35b-a3b"],
    }
}


def test_make_client_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_client raises EnvironmentError when INNKUBE_ACCESS_TOKEN is unset."""
    monkeypatch.delenv("INNKUBE_ACCESS_TOKEN", raising=False)
    with pytest.raises(EnvironmentError, match="INNKUBE_ACCESS_TOKEN"):
        make_client(_VALID_CONFIG)


def test_make_client_returns_openai_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_client returns an OpenAI instance with correct base_url."""
    monkeypatch.setenv("INNKUBE_ACCESS_TOKEN", "test-token")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with patch("agent.llm.client.OpenAI") as mock_openai:
        mock_openai.return_value = MagicMock()
        make_client(_VALID_CONFIG)

    mock_openai.assert_called_once_with(
        api_key="test-token",
        base_url="https://llm-keys.innkube.fim.uni-passau.de/",
    )


def test_make_client_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """OPENAI_BASE_URL env var overrides config.yaml base_url."""
    monkeypatch.setenv("INNKUBE_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://override.example.com/")

    with patch("agent.llm.client.OpenAI") as mock_openai:
        mock_openai.return_value = MagicMock()
        make_client(_VALID_CONFIG)

    mock_openai.assert_called_once_with(
        api_key="test-token",
        base_url="https://override.example.com/",
    )
