"""Tests for the completion dispatchers' config defaulting.

The dispatchers resolve model and timeout from config and forward everything else the
caller sets. max_tokens is the one value that is *defaulted* rather than overridden:
compaction sets its own much smaller summary cap, and clobbering it would spend the
whole summary budget on a full-length completion.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from agent.llm.dispatcher import (
    make_chat_completions_effect,
    make_chat_completions_stream_effect,
)


def _config(max_tokens: int | None = 8192) -> dict[str, Any]:
    config: dict[str, Any] = {
        "llm": {"model": "qwen-agentworld-35b-a3b", "timeout_seconds": 60},
        "loop": {"max_iterations": 30},
    }
    if max_tokens is not None:
        config["loop"]["max_tokens"] = max_tokens
    return config


def _client() -> Mock:
    client = Mock()
    client.chat.completions.create.return_value = Mock(choices=[])
    return client


def _sent_params(client: Mock) -> dict[str, Any]:
    return client.chat.completions.create.call_args.kwargs


def test_max_tokens_defaults_from_loop_config() -> None:
    """A caller that omits max_tokens gets loop.max_tokens."""
    client = _client()
    dispatch = make_chat_completions_effect(client, _config())

    dispatch({"messages": [{"role": "user", "content": "Hi"}]})

    assert _sent_params(client)["max_tokens"] == 8192


def test_caller_max_tokens_wins_over_config() -> None:
    """Compaction's smaller summary cap survives the config default."""
    client = _client()
    dispatch = make_chat_completions_effect(client, _config())

    dispatch({"messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1500})

    assert _sent_params(client)["max_tokens"] == 1500


def test_no_max_tokens_sent_when_config_omits_it() -> None:
    """Without loop.max_tokens the key is left off entirely, not sent as None."""
    client = _client()
    dispatch = make_chat_completions_effect(client, _config(max_tokens=None))

    dispatch({"messages": [{"role": "user", "content": "Hi"}]})

    assert "max_tokens" not in _sent_params(client)


def test_model_and_timeout_override_caller() -> None:
    """Unlike max_tokens, model and timeout come from config regardless of the request."""
    client = _client()
    dispatch = make_chat_completions_effect(client, _config())

    dispatch(
        {
            "messages": [{"role": "user", "content": "Hi"}],
            "model": "gemma4-31b-it",
            "timeout": 5,
        }
    )

    params = _sent_params(client)
    assert params["model"] == "qwen-agentworld-35b-a3b"
    assert params["timeout"] == 60
    assert params["stream"] is False


def test_stream_dispatcher_defaults_max_tokens() -> None:
    """The streaming dispatcher defaults max_tokens the same way."""
    client = _client()
    client.chat.completions.create.return_value = iter([])
    dispatch = make_chat_completions_stream_effect(client, _config())

    list(dispatch({"messages": [{"role": "user", "content": "Hi"}], "stream": True}))

    params = _sent_params(client)
    assert params["max_tokens"] == 8192
    assert params["stream"] is True


def test_stream_dispatcher_caller_max_tokens_wins() -> None:
    """A streaming caller's own cap is preserved."""
    client = _client()
    client.chat.completions.create.return_value = iter([])
    dispatch = make_chat_completions_stream_effect(client, _config())

    list(
        dispatch(
            {
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "max_tokens": 512,
            }
        )
    )

    assert _sent_params(client)["max_tokens"] == 512
