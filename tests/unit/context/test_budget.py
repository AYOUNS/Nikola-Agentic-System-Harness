"""Unit tests for agent.context.budget (token estimation and budget management)."""

from __future__ import annotations

import json

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from agent.context.budget import estimate_tokens, get_budget_info, is_within_budget


def test_estimate_tokens_empty() -> None:
    """Empty messages list returns only overhead."""
    tokens = estimate_tokens([])
    assert tokens == 0  # No messages, no overhead


def test_estimate_tokens_text_only() -> None:
    """Estimate tokens for simple text messages."""
    messages = [
        ChatCompletionSystemMessageParam(
            role="system",
            content="You are a helpful assistant.",
        ),
        ChatCompletionUserMessageParam(
            role="user",
            content="Hello!",
        ),
    ]

    tokens = estimate_tokens(messages, chars_per_token=4.0)

    # "system" (6) + "You are..." (28) + "user" (4) + "Hello!" (6) = 44 chars
    # 44 / 4.0 = 11 tokens + 2 messages * 4 overhead = 19 tokens
    assert tokens == 19


def test_estimate_tokens_with_tool_calls() -> None:
    """Tool calls contribute to token count via JSON serialization."""
    tool_call = ChatCompletionMessageToolCall(
        id="call_123",
        type="function",
        function=Function(name="read_file", arguments='{"path": "test.txt"}'),
    )

    messages = [
        ChatCompletionUserMessageParam(role="user", content="Read the file"),
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=None,
            tool_calls=[tool_call],
        ),
    ]

    tokens = estimate_tokens(messages, chars_per_token=3.5)

    # Should include: roles, content, and serialized tool_calls
    # The exact count depends on JSON serialization, but should be > 0
    assert tokens > 0

    # Tool calls add significant content
    tokens_without_tools = estimate_tokens(
        [ChatCompletionUserMessageParam(role="user", content="Read the file")],
        chars_per_token=3.5,
    )
    assert tokens > tokens_without_tools


def test_estimate_tokens_with_tool_response() -> None:
    """Tool response messages include tool_call_id and content."""
    messages = [
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content="File content here",
        )
    ]

    tokens = estimate_tokens(messages, chars_per_token=3.5)

    # "tool" (4) + "call_123" (8) + "File content here" (17) = 29 chars
    # 29 / 3.5 ≈ 8.3 -> 8 tokens + 1 message * 4 overhead = 12 tokens
    assert tokens >= 10  # Allow some variance


def test_estimate_tokens_with_schemas() -> None:
    """Tool schemas contribute to token count."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Hi"),
    ]

    schema: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        },
    }

    tokens_without_schema = estimate_tokens(messages, schemas=None)
    tokens_with_schema = estimate_tokens(messages, schemas=[schema])

    # Schemas add significant token count
    assert tokens_with_schema > tokens_without_schema
    # Schema JSON is fairly large
    schema_json = json.dumps([schema])
    schema_chars = len(schema_json)
    # Estimate: schema_chars / 3.5 should be roughly the difference
    expected_diff = int(schema_chars / 3.5)
    actual_diff = tokens_with_schema - tokens_without_schema
    # Allow some variance due to rounding
    assert abs(actual_diff - expected_diff) <= 2


def test_estimate_tokens_monotonicity() -> None:
    """Adding more messages always increases token count."""
    msg1 = ChatCompletionUserMessageParam(role="user", content="First")
    msg2 = ChatCompletionUserMessageParam(role="user", content="Second")
    msg3 = ChatCompletionUserMessageParam(role="user", content="Third")

    tokens_1 = estimate_tokens([msg1])
    tokens_2 = estimate_tokens([msg1, msg2])
    tokens_3 = estimate_tokens([msg1, msg2, msg3])

    assert tokens_1 < tokens_2 < tokens_3


def test_estimate_tokens_none_content() -> None:
    """Messages with None content don't cause errors."""
    messages = [
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=None,  # Common when tool_calls present
        )
    ]

    tokens = estimate_tokens(messages)
    # Should count role + overhead, but not crash
    assert tokens >= 0


def test_is_within_budget_fits() -> None:
    """Small conversation fits within budget."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Hi"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Hello!"),
    ]

    config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }

    # Small conversation should easily fit
    assert is_within_budget(messages, None, config) is True


def test_is_within_budget_exceeds() -> None:
    """Large conversation exceeds budget."""
    # Create a very long message
    long_content = "x" * 50000  # 50k chars at 3.5 chars/token = ~14k tokens

    messages = [
        ChatCompletionUserMessageParam(role="user", content=long_content),
    ]

    config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }

    # This should exceed the available budget of 6144 tokens
    assert is_within_budget(messages, None, config) is False


def test_is_within_budget_with_default_config() -> None:
    """Budget check works with missing config keys (uses defaults)."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Test"),
    ]

    # Empty config should use defaults
    assert is_within_budget(messages, None, {}) is True


def test_get_budget_info_structure() -> None:
    """Budget info returns expected keys and structure."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Hello world"),
    ]

    config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }

    info = get_budget_info(messages, None, config)

    # Check all expected keys present
    assert "current_tokens" in info
    assert "window_tokens" in info
    assert "reserve_output" in info
    assert "available" in info
    assert "remaining" in info
    assert "utilization_pct" in info

    # Check types
    assert isinstance(info["current_tokens"], int)
    assert isinstance(info["window_tokens"], int)
    assert isinstance(info["reserve_output"], int)
    assert isinstance(info["available"], int)
    assert isinstance(info["remaining"], int)
    assert isinstance(info["utilization_pct"], int)

    # Check logical relationships
    assert info["available"] == info["window_tokens"] - info["reserve_output"]
    assert info["remaining"] == info["available"] - info["current_tokens"]
    assert 0 <= info["utilization_pct"] <= 100


def test_get_budget_info_utilization() -> None:
    """Budget utilization percentage is calculated correctly."""
    # Create a message that uses roughly half the available budget
    # Available = 8192 - 2048 = 6144 tokens
    # Target = ~3000 tokens = 10500 chars at 3.5 chars/token
    content = "x" * 10500

    messages = [
        ChatCompletionUserMessageParam(role="user", content=content),
    ]

    config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 3.5,
        }
    }

    info = get_budget_info(messages, None, config)

    # Should be roughly 50% utilization
    assert 45 <= info["utilization_pct"] <= 55


def test_estimate_tokens_consistent_with_budget_info() -> None:
    """estimate_tokens and get_budget_info should use same calculation."""
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="System prompt"),
        ChatCompletionUserMessageParam(role="user", content="User message"),
    ]

    config = {
        "context": {
            "window_tokens": 8192,
            "reserve_output_tokens": 2048,
            "chars_per_token": 4.0,
        }
    }

    direct_estimate = estimate_tokens(messages, chars_per_token=4.0)
    info = get_budget_info(messages, None, config)

    assert direct_estimate == info["current_tokens"]


def test_estimate_tokens_with_multiple_schemas() -> None:
    """Multiple tool schemas accumulate correctly."""
    messages = [ChatCompletionUserMessageParam(role="user", content="Test")]

    schema1: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "tool1",
            "description": "First tool",
            "parameters": {"type": "object"},
        },
    }

    schema2: ChatCompletionToolParam = {
        "type": "function",
        "function": {
            "name": "tool2",
            "description": "Second tool",
            "parameters": {"type": "object"},
        },
    }

    tokens_no_schema = estimate_tokens(messages, schemas=None)
    tokens_one_schema = estimate_tokens(messages, schemas=[schema1])
    tokens_two_schemas = estimate_tokens(messages, schemas=[schema1, schema2])

    # More schemas should mean more tokens
    assert tokens_no_schema < tokens_one_schema < tokens_two_schemas
