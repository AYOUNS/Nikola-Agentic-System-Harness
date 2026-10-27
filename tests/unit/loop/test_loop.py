"""Unit tests for agent.loop utilities (parsing, execution, streaming).

Tests for the main ReActEngine are in tests/unit/engine/test_core.py.
"""

from __future__ import annotations

import json

import pytest
from openai.types.chat import (
    ChatCompletionChunk,
)
from openai.types.chat.chat_completion_chunk import (
    Choice as ChunkChoice,
)
from openai.types.chat.chat_completion_chunk import (
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
)
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from agent.loop_utils import (
    accumulate_stream,
    execute_tool_call,
    parse_tool_arguments,
)
from agent.tools import ToolRegistry

# =============================================================================
# Utility Function Tests
# =============================================================================

def test_parse_tool_arguments_valid() -> None:
    """parse_tool_arguments extracts a dict from valid JSON."""
    args, err = parse_tool_arguments('{"key": "value"}')
    assert args == {"key": "value"}
    assert err is None


def test_parse_tool_arguments_invalid_json() -> None:
    """parse_tool_arguments returns an error for malformed JSON."""
    args, err = parse_tool_arguments("{key: bad}")
    assert args is None
    assert err is not None
    assert "Invalid JSON" in err


def test_parse_tool_arguments_non_object() -> None:
    """parse_tool_arguments rejects non-dict top-level JSON."""
    args, err = parse_tool_arguments('"just a string"')
    assert args is None
    assert err is not None
    assert "must be a JSON object" in err


@pytest.mark.asyncio
async def test_execute_tool_call_handles_malformed_args() -> None:
    """execute_tool_call reports parse errors as tool observations."""
    registry = ToolRegistry()

    tc = ChatCompletionMessageToolCall(
        id="call_bad",
        type="function",
        function=Function(name="some_tool", arguments="{bad json"),
    )
    outcome = await execute_tool_call(tc, registry)
    assert outcome.message["role"] == "tool"
    assert outcome.message["tool_call_id"] == "call_bad"
    assert "Error" in outcome.message["content"]
    assert "Invalid JSON" in outcome.message["content"]
    assert outcome.success is False
    assert outcome.permission_outcome == "none"


# =============================================================================
# Stream Accumulation Tests
# =============================================================================

def test_accumulate_stream_reassembles_split_tool_call() -> None:
    """accumulate_stream reconstructs tool calls from delta fragments."""
    chunks = [
        ChatCompletionChunk(
            id="c1",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(
                        tool_calls=[
                            ChoiceDeltaToolCall(
                                index=0,
                                id="call_1",
                                function=ChoiceDeltaToolCallFunction(
                                    name="write_file",
                                    arguments='{"path": "',
                                ),
                            )
                        ]
                    ),
                    finish_reason=None,
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c2",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(
                        tool_calls=[
                            ChoiceDeltaToolCall(
                                index=0,
                                function=ChoiceDeltaToolCallFunction(
                                    arguments='foo.txt"}',
                                ),
                            )
                        ]
                    ),
                    finish_reason=None,
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c3",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(),
                    finish_reason="tool_calls",
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
    ]

    message, finish_reason = accumulate_stream(iter(chunks))
    assert message is not None
    assert finish_reason == "tool_calls"
    assert len(message.tool_calls or []) == 1
    tc = message.tool_calls[0]
    assert tc.function.name == "write_file"
    assert json.loads(tc.function.arguments) == {"path": "foo.txt"}


def test_accumulate_stream_forwards_content_tokens() -> None:
    """accumulate_stream calls on_token for each content fragment."""
    tokens_seen = []

    def capture_token(text: str) -> None:
        tokens_seen.append(text)

    chunks = [
        ChatCompletionChunk(
            id="c1",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(content="Hello "),
                    finish_reason=None,
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c2",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(content="world!"),
                    finish_reason=None,
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c3",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(),
                    finish_reason="stop",
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
    ]

    message, _ = accumulate_stream(iter(chunks), on_token=capture_token)
    assert message is not None
    assert message.content == "Hello world!"
    assert tokens_seen == ["Hello ", "world!"]


def test_accumulate_stream_forwards_reasoning_without_polluting_content() -> None:
    """Thinking fragments go to on_reasoning sink only; kept out of the message."""
    content_seen = []
    reasoning_seen = []

    chunks = [
        ChatCompletionChunk(
            id="c1",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(
                        reasoning_content="Let me think..."  # type: ignore[arg-type]
                    ),
                    finish_reason=None,
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c2",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(content="The answer is 42."),
                    finish_reason=None,
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
        ChatCompletionChunk(
            id="c3",
            choices=[
                ChunkChoice(
                    index=0,
                    delta=ChoiceDelta(),
                    finish_reason="stop",
                )
            ],
            created=0,
            model="test",
            object="chat.completion.chunk",
        ),
    ]

    message, _ = accumulate_stream(
        iter(chunks),
        on_token=content_seen.append,
        on_reasoning=reasoning_seen.append,
    )
    assert message is not None
    assert message.content == "The answer is 42."
    assert content_seen == ["The answer is 42."]
    assert reasoning_seen == ["Let me think..."]
