"""Unit tests for /context command."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from agent.cli.commands.builtin import compact_command, context_command
from agent.cli.session import Session
from agent.cli.ui import ConsoleUI


def test_context_command_shows_budget_info() -> None:
    """The /context command displays budget breakdown via UI."""
    # Create a real config dict (Mock won't work with get())
    config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }

    # Create a mock session with minimal messages
    session = Mock(spec=Session)
    session.messages = [
        ChatCompletionSystemMessageParam(role="system", content="System prompt"),
        ChatCompletionUserMessageParam(role="user", content="Hello"),
    ]
    session.tools = Mock()
    session.tools.get_schemas.return_value = []
    session.config = config
    session.ui = Mock(spec=ConsoleUI)

    # Execute the command
    context_command(session, "")

    # Verify table was called with budget info
    session.ui.table.assert_called_once()
    call_args = session.ui.table.call_args

    # Check title
    assert call_args[0][0] == "Context Budget"

    # Check rows contain expected keys
    rows = call_args[0][1]
    row_dict = {key: value for key, value in rows if key}

    assert "Window size" in row_dict
    assert "tokens" in row_dict["Window size"]
    assert "Current usage" in row_dict
    assert "Remaining" in row_dict

    # Utilization is drawn as a meter, not a table row.
    session.ui.usage_bar.assert_called_once()
    label, pct = session.ui.usage_bar.call_args[0][:2]
    assert label == "Utilization"
    assert 0 <= pct <= 100


def test_context_command_shows_hint_when_near_capacity() -> None:
    """The /context command shows a hint when utilization is high."""
    session = Mock(spec=Session)

    # Create a long message to push utilization high
    long_content = "x" * 30000  # ~8500 tokens at 3.5 chars/token

    session.messages = [
        ChatCompletionUserMessageParam(role="user", content=long_content),
    ]
    session.tools = Mock()
    session.tools.get_schemas.return_value = []
    session.config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }
    session.ui = Mock(spec=ConsoleUI)

    # Execute the command
    context_command(session, "")

    # Should show hint because utilization > 80%
    session.ui.hint.assert_called_once()
    hint_msg = session.ui.hint.call_args[0][0]
    assert "nearing capacity" in hint_msg.lower()


def test_context_command_no_hint_when_under_capacity() -> None:
    """The /context command doesn't show hint when utilization is low."""
    session = Mock(spec=Session)
    session.messages = [
        ChatCompletionUserMessageParam(role="user", content="Short message"),
    ]
    session.tools = Mock()
    session.tools.get_schemas.return_value = []
    session.config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }
    session.ui = Mock(spec=ConsoleUI)

    # Execute the command
    context_command(session, "")

    # Should not show hint because utilization is low
    session.ui.hint.assert_not_called()


def test_context_command_includes_tool_schemas() -> None:
    """The /context command accounts for tool schemas in token count."""
    session = Mock(spec=Session)
    session.messages = [
        ChatCompletionUserMessageParam(role="user", content="Test"),
    ]

    # Add some tool schemas
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "tool1",
                "description": "First tool",
                "parameters": {"type": "object"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tool2",
                "description": "Second tool",
                "parameters": {"type": "object"},
            },
        },
    ]

    session.tools = Mock()
    session.tools.get_schemas.return_value = tool_schemas
    session.config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }
    session.ui = Mock(spec=ConsoleUI)

    # Execute the command
    context_command(session, "")

    # Verify it was called (schemas were included in estimation)
    session.ui.table.assert_called_once()


def test_context_command_uses_default_config_when_missing() -> None:
    """The /context command works even with empty config."""
    session = Mock(spec=Session)
    session.messages = [
        ChatCompletionUserMessageParam(role="user", content="Test"),
    ]
    session.tools = Mock()
    session.tools.get_schemas.return_value = []
    session.config = {}  # Empty config should use defaults
    session.ui = Mock(spec=ConsoleUI)

    # Should not raise an error
    context_command(session, "")

    # Should still render table
    session.ui.table.assert_called_once()


def _compact_session(messages: list) -> Mock:
    """A session whose /compact run has a model configured, so summarization is attempted."""
    session = Mock(spec=Session)
    session.messages = messages
    session.tools = Mock()
    session.tools.get_schemas.return_value = []
    session.config = {
        "llm": {"model": "qwen-agentworld-35b-a3b"},
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        },
    }
    session.ui = Mock(spec=ConsoleUI)
    return session


def _conversation(turns: int = 6) -> list:
    """A system prompt followed by `turns` user/assistant exchanges."""
    messages = [ChatCompletionSystemMessageParam(role="system", content="You are an agent")]
    for turn in range(turns):
        messages.append(
            ChatCompletionUserMessageParam(role="user", content=f"question {turn} " + "q" * 400)
        )
        messages.append({"role": "assistant", "content": f"answer {turn} " + "a" * 400})
    return messages


def _stub_summarizer(monkeypatch: pytest.MonkeyPatch, content: str | None = "A summary.") -> Mock:
    """Patch the LLM plumbing /compact builds, returning the dispatch mock."""
    dispatch = Mock()
    dispatch.return_value.choices = [Mock(message=Mock(content=content), finish_reason="stop")]
    monkeypatch.setattr("agent.llm.client.make_client", lambda config: Mock())
    monkeypatch.setattr(
        "agent.llm.dispatcher.make_chat_completions_effect",
        lambda client, config: dispatch,
    )
    return dispatch


def test_compact_command_replaces_conversation_with_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole conversation collapses to the system prompt plus one summary."""
    _stub_summarizer(monkeypatch, "User asked 6 questions about config.yaml.")
    session = _compact_session(_conversation())

    compact_command(session, "")

    assert len(session.messages) == 2
    assert session.messages[0]["content"] == "You are an agent"
    assert session.messages[1]["content"].startswith("[Summary of earlier conversation")
    assert "User asked 6 questions about config.yaml." in session.messages[1]["content"]

    report = session.ui.info.call_args[0][0]
    assert "Compacted 12 messages" in report
    session.ui.usage_bar.assert_called_once()


def test_compact_command_runs_even_when_well_under_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual compaction is unconditional — a short conversation still compacts."""
    _stub_summarizer(monkeypatch)
    session = _compact_session(
        [
            ChatCompletionSystemMessageParam(role="system", content="You are an agent"),
            ChatCompletionUserMessageParam(role="user", content="hello"),
        ]
    )

    compact_command(session, "")

    assert len(session.messages) == 2
    assert session.messages[1]["content"].startswith("[Summary of earlier conversation")


def test_compact_command_passes_instructions_to_summarizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free text after /compact steers what the summary preserves."""
    dispatch = _stub_summarizer(monkeypatch)
    session = _compact_session(_conversation())

    compact_command(session, "keep the config.yaml findings")

    prompt = dispatch.call_args[0][0]["messages"][0]["content"]
    assert "keep the config.yaml findings" in prompt


def test_compact_command_leaves_conversation_intact_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A summarizer error must not cost the conversation it was condensing."""
    def explode(config):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("agent.llm.client.make_client", explode)
    session = _compact_session(_conversation())
    before = list(session.messages)

    compact_command(session, "")

    assert session.messages == before
    assert "Could not summarize" in session.ui.hint.call_args[0][0]
    session.ui.usage_bar.assert_not_called()


def test_compact_command_keeps_conversation_on_empty_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty completion leaves the conversation alone rather than erasing it."""
    _stub_summarizer(monkeypatch, content=None)
    session = _compact_session(_conversation())
    before = list(session.messages)

    compact_command(session, "")

    assert session.messages == before
    assert "returned nothing" in session.ui.hint.call_args[0][0]


def test_compact_command_reports_empty_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only a system prompt there is nothing to summarize."""
    _stub_summarizer(monkeypatch)
    session = _compact_session(
        [ChatCompletionSystemMessageParam(role="system", content="You are an agent")]
    )

    compact_command(session, "")

    assert "conversation is empty" in session.ui.info.call_args[0][0]
    assert len(session.messages) == 1


def test_compact_command_reports_a_summary_that_grew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Summarizing two short messages can cost tokens; the report says so plainly."""
    _stub_summarizer(monkeypatch, "A long summary. " * 200)
    session = _compact_session(
        [
            ChatCompletionSystemMessageParam(role="system", content="You are an agent"),
            ChatCompletionUserMessageParam(role="user", content="hi"),
        ]
    )

    compact_command(session, "")

    report = session.ui.info.call_args[0][0]
    assert "larger than what it replaced" in report
    assert "-" not in report
