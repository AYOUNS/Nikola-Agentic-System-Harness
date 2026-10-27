"""Unit tests for agent.llm.models (model info and context window detection)."""

from __future__ import annotations

from unittest.mock import Mock

from agent.llm.models import get_context_config, get_model_context_window


def test_get_model_context_window_success() -> None:
    """get_model_context_window extracts context_length from API response."""
    # Create mock client and model response
    client = Mock()
    mock_model = Mock()
    mock_model.id = "qwen-agentworld-35b-a3b"
    mock_model.context_length = 32768

    mock_response = Mock()
    mock_response.data = [mock_model]

    client.models.list.return_value = mock_response

    # Get context window
    window = get_model_context_window(client, "qwen-agentworld-35b-a3b")

    assert window == 32768
    client.models.list.assert_called_once()


def test_get_model_context_window_max_model_len() -> None:
    """get_model_context_window uses max_model_len if context_length missing."""
    client = Mock()
    mock_model = Mock()
    mock_model.id = "test-model"
    # No context_length, but has max_model_len
    mock_model.spec = []  # Make hasattr(model, "context_length") return False
    delattr(mock_model, "context_length")
    mock_model.max_model_len = 16384

    mock_response = Mock()
    mock_response.data = [mock_model]

    client.models.list.return_value = mock_response

    window = get_model_context_window(client, "test-model")

    assert window == 16384


def test_get_model_context_window_model_not_found() -> None:
    """get_model_context_window returns default when model not in list."""
    client = Mock()
    mock_model = Mock()
    mock_model.id = "other-model"

    mock_response = Mock()
    mock_response.data = [mock_model]

    client.models.list.return_value = mock_response

    # Request a model that doesn't exist
    window = get_model_context_window(client, "nonexistent-model")

    assert window == 8192  # Default


def test_get_model_context_window_no_context_field() -> None:
    """get_model_context_window returns default when context_length missing."""
    client = Mock()
    mock_model = Mock()
    mock_model.id = "test-model"
    # Model exists but has no context_length or max_model_len
    delattr(mock_model, "context_length")
    delattr(mock_model, "max_model_len")

    mock_response = Mock()
    mock_response.data = [mock_model]

    client.models.list.return_value = mock_response

    window = get_model_context_window(client, "test-model")

    assert window == 8192  # Default


def test_get_model_context_window_api_error() -> None:
    """get_model_context_window returns default when API call fails."""
    client = Mock()
    client.models.list.side_effect = Exception("API Error")

    window = get_model_context_window(client, "any-model")

    assert window == 8192  # Default


def test_get_context_config_with_api_window() -> None:
    """get_context_config merges API window with config settings."""
    # Mock client that returns 32K window
    client = Mock()
    mock_model = Mock()
    mock_model.id = "qwen-agentworld-35b-a3b"
    mock_model.context_length = 32768

    mock_response = Mock()
    mock_response.data = [mock_model]
    client.models.list.return_value = mock_response

    # Config with settings but no window_tokens
    config = {
        "llm": {"model": "qwen-agentworld-35b-a3b"},
        "context": {
            "reserve_output_tokens": 4096,
            "chars_per_token": 4.0,
            "max_observation_chars": 20000,
        },
    }

    result = get_context_config(client, config)

    # Should use API window and config settings
    assert result["window_tokens"] == 32768  # From API
    assert result["reserve_output_tokens"] == 4096  # From config
    assert result["chars_per_token"] == 4.0  # From config
    assert result["max_observation_chars"] == 20000  # From config


def test_get_context_config_with_defaults() -> None:
    """get_context_config uses defaults when config values missing."""
    # Mock client with 8K default
    client = Mock()
    client.models.list.side_effect = Exception("API Error")

    # Minimal config
    config = {"llm": {"model": "test-model"}}

    result = get_context_config(client, config)

    # Should use all defaults
    assert result["window_tokens"] == 8192
    assert result["reserve_output_tokens"] == 2048
    assert result["chars_per_token"] == 3.5
    assert result["max_observation_chars"] == 10000


def test_get_context_config_partial_config() -> None:
    """get_context_config mixes provided and default values."""
    client = Mock()
    mock_model = Mock()
    mock_model.id = "test-model"
    mock_model.context_length = 16384

    mock_response = Mock()
    mock_response.data = [mock_model]
    client.models.list.return_value = mock_response

    # Config with only some settings
    config = {
        "llm": {"model": "test-model"},
        "context": {
            "reserve_output_tokens": 1024,
            # Missing chars_per_token and max_observation_chars
        },
    }

    result = get_context_config(client, config)

    assert result["window_tokens"] == 16384  # From API
    assert result["reserve_output_tokens"] == 1024  # From config
    assert result["chars_per_token"] == 3.5  # Default
    assert result["max_observation_chars"] == 10000  # Default


def test_get_context_config_prefers_configured_window() -> None:
    """An explicit window_tokens overrides whatever the API reports."""
    client = Mock()
    mock_model = Mock()
    mock_model.id = "qwen-agentworld-35b-a3b"
    mock_model.context_length = 8192

    mock_response = Mock()
    mock_response.data = [mock_model]
    client.models.list.return_value = mock_response

    config = {
        "llm": {"model": "qwen-agentworld-35b-a3b"},
        "context": {"window_tokens": 32768},
    }

    result = get_context_config(client, config)

    assert result["window_tokens"] == 32768
    # The lookup is skipped entirely, so a gateway that cannot answer is never asked.
    client.models.list.assert_not_called()


def test_get_context_config_falls_back_to_api_when_unset() -> None:
    """Without a configured window, detection still runs."""
    client = Mock()
    mock_model = Mock()
    mock_model.id = "test-model"
    mock_model.context_length = 16384

    mock_response = Mock()
    mock_response.data = [mock_model]
    client.models.list.return_value = mock_response

    config = {"llm": {"model": "test-model"}, "context": {"chars_per_token": 4.0}}

    result = get_context_config(client, config)

    assert result["window_tokens"] == 16384
    client.models.list.assert_called_once()
