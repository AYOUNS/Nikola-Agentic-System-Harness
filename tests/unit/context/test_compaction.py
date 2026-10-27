"""Unit tests for agent.context.compaction (context compaction strategies)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from agent.context.compaction import (
    _SUMMARY_HEADER,
    SummaryTruncated,
    compact,
    compact_tier1_truncate_observations,
    compact_tier2_drop_turns,
    find_summary_index,
    find_turn_boundaries,
    render_transcript,
    replace_dropped_with_summary,
    summarize_messages,
    summary_message,
    truncate_observation,
)


def test_truncate_observation_under_limit() -> None:
    """Observations under limit are returned unchanged."""
    content = "Short content"
    result = truncate_observation(content, max_chars=100)
    assert result == content
    assert "[truncated]" not in result


def test_truncate_observation_over_limit() -> None:
    """Observations over limit are truncated with marker."""
    content = "x" * 1000
    result = truncate_observation(content, max_chars=100)

    assert len(result) <= 100  # The marker counts against the limit
    assert result.startswith("x" * 50)
    assert "[truncated: showing first" in result

    # Truncating the result again changes nothing, so repeated compaction passes neither
    # nibble away at kept content nor report work they did not do.
    assert truncate_observation(result, max_chars=100) == result


def test_tier1_no_truncation_needed() -> None:
    """Tier 1 does nothing when all observations are under limit."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Read file"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Ok"),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content="Short file content",
        ),
    ]

    original_len = len(messages[2]["content"])  # type: ignore
    count = compact_tier1_truncate_observations(messages, max_observation_chars=1000)

    assert count == 0
    assert len(messages[2]["content"]) == original_len  # type: ignore


def test_tier1_truncates_oversized_observations() -> None:
    """Tier 1 truncates tool messages exceeding max_chars."""
    long_content = "x" * 10000

    messages = [
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_1",
            content=long_content,
        ),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_2",
            content="short",
        ),
    ]

    count = compact_tier1_truncate_observations(messages, max_observation_chars=100)

    assert count == 1  # Only one truncated
    assert "[truncated:" in messages[0]["content"]  # type: ignore
    assert "short" == messages[1]["content"]  # Second unchanged


def test_tier1_only_affects_tool_messages() -> None:
    """Tier 1 only truncates tool role messages, not user/assistant."""
    long_content = "x" * 10000

    messages = [
        ChatCompletionUserMessageParam(role="user", content=long_content),
        ChatCompletionAssistantMessageParam(role="assistant", content=long_content),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content=long_content,
        ),
    ]

    count = compact_tier1_truncate_observations(messages, max_observation_chars=100)

    # Only the tool message should be truncated
    assert count == 1
    assert len(messages[0]["content"]) == 10000  # type: ignore  # User unchanged
    assert len(messages[1]["content"]) == 10000  # type: ignore  # Assistant unchanged
    assert "[truncated:" in messages[2]["content"]  # type: ignore  # Tool truncated


def test_find_turn_boundaries_simple() -> None:
    """Find boundaries for simple user-assistant exchanges."""
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="System"),
        ChatCompletionUserMessageParam(role="user", content="Hi"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Hello"),
        ChatCompletionUserMessageParam(role="user", content="Bye"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Goodbye"),
    ]

    turns = find_turn_boundaries(messages)

    # Each message is its own turn (no tool calls)
    assert turns == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)]


def test_find_turn_boundaries_with_tool_calls() -> None:
    """Tool-calling assistant message groups with its tool responses."""
    tool_call = ChatCompletionMessageToolCall(
        id="call_123",
        type="function",
        function=Function(name="read_file", arguments="{}"),
    )

    messages = [
        ChatCompletionUserMessageParam(role="user", content="Read file"),
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=None,
            tool_calls=[tool_call],
        ),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content="file content",
        ),
        ChatCompletionAssistantMessageParam(role="assistant", content="Done"),
    ]

    turns = find_turn_boundaries(messages)

    # Turn 0: user message (idx 0)
    # Turn 1: assistant with tool_calls + tool response (idx 1-2)
    # Turn 2: final assistant message (idx 3)
    assert turns == [(0, 0), (1, 2), (3, 3)]


def test_find_turn_boundaries_multiple_tool_responses() -> None:
    """Assistant message with multiple tool calls groups all responses."""
    tool_call1 = ChatCompletionMessageToolCall(
        id="call_1",
        type="function",
        function=Function(name="tool1", arguments="{}"),
    )
    tool_call2 = ChatCompletionMessageToolCall(
        id="call_2",
        type="function",
        function=Function(name="tool2", arguments="{}"),
    )

    messages = [
        ChatCompletionUserMessageParam(role="user", content="Do things"),
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=None,
            tool_calls=[tool_call1, tool_call2],
        ),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_1",
            content="result1",
        ),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_2",
            content="result2",
        ),
        ChatCompletionAssistantMessageParam(role="assistant", content="Done"),
    ]

    turns = find_turn_boundaries(messages)

    # Turn 0: user (idx 0)
    # Turn 1: assistant + 2 tool responses (idx 1-3)
    # Turn 2: final assistant (idx 4)
    assert turns == [(0, 0), (1, 3), (4, 4)]


def test_tier2_drops_oldest_turns() -> None:
    """Tier 2 drops oldest turns to fit budget."""
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="System"),
        ChatCompletionUserMessageParam(role="user", content="Message 1"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Reply 1"),
        ChatCompletionUserMessageParam(role="user", content="Message 2"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Reply 2"),
        ChatCompletionUserMessageParam(role="user", content="Message 3"),
    ]

    def estimate_fn(msgs):
        # Simple estimate: 10 tokens per message
        return len(msgs) * 10

    # Current: 6 messages * 10 = 60 tokens
    # Target: 40 tokens = 4 messages
    # Should drop 2 oldest droppable messages (Message 1 + Reply 1)

    turns_dropped, _ = compact_tier2_drop_turns(messages, target_tokens=40, estimate_fn=estimate_fn)

    # Should drop turn 1 and 2 (user message 1 and assistant reply 1)
    assert turns_dropped >= 1
    assert len(messages) <= 4  # 4 or fewer messages
    # System message should still be there (protected)
    assert messages[0]["role"] == "system"
    # Most recent user message should be there (protected)
    assert messages[-1]["content"] == "Message 3"


def test_tier2_preserves_system_message() -> None:
    """Tier 2 never drops index 0 system message."""
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="System"),
        ChatCompletionUserMessageParam(role="user", content="Old message"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Old reply"),
        ChatCompletionUserMessageParam(role="user", content="New message"),
    ]

    def estimate_fn(msgs):
        return len(msgs) * 10

    # Force aggressive dropping
    compact_tier2_drop_turns(messages, target_tokens=10, estimate_fn=estimate_fn)

    # System message must remain
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "System"


def test_tier2_preserves_most_recent_user_message() -> None:
    """Tier 2 preserves the most recent user message."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Old message"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Old reply"),
        ChatCompletionUserMessageParam(role="user", content="New message"),
    ]

    def estimate_fn(msgs):
        return len(msgs) * 10

    compact_tier2_drop_turns(messages, target_tokens=15, estimate_fn=estimate_fn)

    # Most recent user message must remain
    assert any(msg.get("content") == "New message" for msg in messages)


def test_tier2_preserves_tool_call_pairing() -> None:
    """Tier 2 never splits assistant+tool_calls from its tool responses."""
    tool_call = ChatCompletionMessageToolCall(
        id="call_123",
        type="function",
        function=Function(name="tool", arguments="{}"),
    )

    messages = [
        ChatCompletionUserMessageParam(role="user", content="Old"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Old reply"),
        ChatCompletionUserMessageParam(role="user", content="Do tool"),
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=None,
            tool_calls=[tool_call],
        ),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content="tool result",
        ),
        ChatCompletionUserMessageParam(role="user", content="Recent"),
    ]

    def estimate_fn(msgs):
        return len(msgs) * 10

    # Target forces dropping but should keep pairing intact
    compact_tier2_drop_turns(messages, target_tokens=30, estimate_fn=estimate_fn)

    # Check pairing: if assistant with tool_calls is present, tool response must be too
    has_assistant_with_tools = False
    has_tool_response = False

    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            has_assistant_with_tools = True
        if msg.get("role") == "tool" and msg.get("tool_call_id") == "call_123":
            has_tool_response = True

    # Either both present or both absent
    assert has_assistant_with_tools == has_tool_response


def test_compact_tier1_only() -> None:
    """Compact with tier 1 only when truncation is enough."""
    long_content = "x" * 10000

    messages = [
        ChatCompletionUserMessageParam(role="user", content="Read file"),
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content=long_content,
        ),
    ]

    def estimate_fn(msgs):
        # Count total characters as proxy for tokens
        total_chars = sum(len(str(msg.get("content", ""))) for msg in msgs)
        return total_chars // 4  # ~4 chars per token

    # Before: ~2500 tokens (10000 chars / 4)
    # After tier 1 truncation: messages should fit target

    result = compact(
        messages,
        target_tokens=500,
        estimate_fn=estimate_fn,
        max_observation_chars=1000,
    )

    assert result["observations_truncated"] == 1
    assert result["turns_dropped"] == 0  # Tier 1 was enough
    assert "[truncated:" in messages[1]["content"]  # type: ignore


def test_compact_uses_tier2_when_tier1_insufficient() -> None:
    """Compact falls through to tier 2 when truncation isn't enough."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Message 1"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Reply 1"),
        ChatCompletionUserMessageParam(role="user", content="Message 2"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Reply 2"),
        ChatCompletionUserMessageParam(role="user", content="Message 3"),
    ]

    def estimate_fn(msgs):
        return len(msgs) * 10

    # Current: 5 messages = 50 tokens, target: 30 tokens
    result = compact(messages, target_tokens=30, estimate_fn=estimate_fn)

    # Should drop some turns
    assert result["turns_dropped"] > 0
    assert len(messages) < 5


def test_compact_idempotent_when_under_budget() -> None:
    """Compact does nothing when already under budget."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Hi"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Hello"),
    ]

    def estimate_fn(msgs):
        return len(msgs) * 10

    # 2 messages = 20 tokens, target = 100 tokens
    result = compact(messages, target_tokens=100, estimate_fn=estimate_fn)

    assert result["observations_truncated"] == 0
    assert result["turns_dropped"] == 0
    assert len(messages) == 2  # Nothing dropped


def test_compact_returns_dropped_messages() -> None:
    """Compact returns the list of dropped messages."""
    messages = [
        ChatCompletionUserMessageParam(role="user", content="Message 1"),
        ChatCompletionAssistantMessageParam(role="assistant", content="Reply 1"),
        ChatCompletionUserMessageParam(role="user", content="Message 2"),
    ]

    def estimate_fn(msgs):
        return len(msgs) * 10

    result = compact(messages, target_tokens=15, estimate_fn=estimate_fn)

    # Should have dropped at least one message
    assert len(result["dropped_messages"]) > 0
    # Dropped messages should contain the old conversation
    dropped_content = [msg.get("content") for msg in result["dropped_messages"]]
    assert any("Message 1" in str(c) or "Reply 1" in str(c) for c in dropped_content)


def test_compact_single_oversized_observation_cannot_exceed_budget() -> None:
    """A single observation truncated to max_chars stays within reasonable bounds."""
    huge_content = "x" * 100000  # 100k characters

    messages = [
        ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id="call_123",
            content=huge_content,
        ),
    ]

    def estimate_fn(msgs):
        total_chars = sum(len(str(msg.get("content", ""))) for msg in msgs)
        return total_chars // 4

    # max_observation_chars=1000 means after truncation, content ~1000 chars = ~250 tokens
    result = compact(messages, target_tokens=500, estimate_fn=estimate_fn, max_observation_chars=1000)

    assert result["observations_truncated"] == 1
    final_content_len = len(messages[0]["content"])  # type: ignore
    # Should be around 1000 chars + truncation marker
    assert final_content_len < 1200  # Some overhead for marker, but bounded


def test_render_transcript_caps_long_observations() -> None:
    """The transcript is itself a prompt, so each message is capped."""
    messages = [
        {"role": "user", "content": "find the config"},
        {"role": "tool", "tool_call_id": "c1", "content": "z" * 9000},
    ]

    transcript = render_transcript(messages)

    assert "user: find the config" in transcript
    assert len(transcript) < 5000
    assert transcript.endswith("…")


def test_render_transcript_names_tool_calls() -> None:
    """A tool call is recorded by name, so the summary can say what ran."""
    messages = [
        {
            "role": "assistant",
            "content": "Reading it",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
    ]

    assert "called: read_file" in render_transcript(messages)


def test_summarize_messages_returns_model_text() -> None:
    """summarize_messages sends the transcript and returns the completion text."""
    dispatch = Mock()
    dispatch.return_value.choices = [
        Mock(message=Mock(content="  User wanted X. Edited config.yaml.  "))
    ]

    summary = summarize_messages([{"role": "user", "content": "do X"}], dispatch)

    assert summary == "User wanted X. Edited config.yaml."

    request = dispatch.call_args[0][0]
    assert request["messages"][0]["role"] == "system"
    assert "condensing" in request["messages"][0]["content"]
    assert "do X" in request["messages"][1]["content"]
    assert request["max_tokens"] == 1500


def test_summarize_messages_handles_empty_completion() -> None:
    """An empty choices array yields no summary rather than raising."""
    dispatch = Mock()
    dispatch.return_value.choices = []

    assert summarize_messages([{"role": "user", "content": "hi"}], dispatch) == ""


def test_summarize_messages_skips_empty_input() -> None:
    """Nothing dropped means no API call."""
    dispatch = Mock()

    assert summarize_messages([], dispatch) == ""
    dispatch.assert_not_called()


def test_find_summary_index_locates_inserted_summary() -> None:
    """An inserted summary is findable, so repeated compactions fold into one."""
    messages = [
        {"role": "system", "content": "You are an agent"},
        summary_message("Earlier: user asked for X."),
        {"role": "user", "content": "and now Y"},
    ]

    assert find_summary_index(messages) == 1
    assert find_summary_index(messages[:1]) is None


def test_summarize_messages_raises_when_cap_consumed_by_reasoning() -> None:
    """A reasoning model that runs out of budget mid-thought reports why.

    Regression: the served Qwen model emits reasoning_content first, so a small cap returned
    finish_reason="length" with content=None. That degraded silently to a plain drop.
    """
    dispatch = Mock()
    dispatch.return_value.choices = [
        Mock(message=Mock(content=None), finish_reason="length")
    ]

    with pytest.raises(SummaryTruncated, match="summary_max_tokens"):
        summarize_messages([{"role": "user", "content": "hi"}], dispatch, max_tokens=500)


def test_summarize_messages_forwards_token_cap() -> None:
    """The configured cap reaches the request."""
    dispatch = Mock()
    dispatch.return_value.choices = [Mock(message=Mock(content="ok"), finish_reason="stop")]

    summarize_messages([{"role": "user", "content": "hi"}], dispatch, max_tokens=2200)

    assert dispatch.call_args[0][0]["max_tokens"] == 2200


def test_summarize_messages_disables_reasoning_by_default() -> None:
    """Reasoning is off for the summary call, and omitted entirely when unset."""
    dispatch = Mock()
    dispatch.return_value.choices = [Mock(message=Mock(content="ok"), finish_reason="stop")]

    summarize_messages([{"role": "user", "content": "hi"}], dispatch)
    assert dispatch.call_args[0][0]["reasoning_effort"] == "none"

    # An empty setting leaves the parameter out, for a provider that rejects it.
    summarize_messages([{"role": "user", "content": "hi"}], dispatch, reasoning_effort="")
    assert "reasoning_effort" not in dispatch.call_args[0][0]


def _summary_dispatch(content: str = "Recap of the dropped turns.") -> Mock:
    """A dispatch returning one usable summary completion."""
    dispatch = Mock()
    dispatch.return_value.choices = [Mock(message=Mock(content=content), finish_reason="stop")]
    return dispatch


def test_replace_dropped_inserts_after_system_prompt() -> None:
    """The summary is inserted as a user message after the system message."""
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="You are an agent"),
        ChatCompletionUserMessageParam(role="user", content="still here"),
    ]
    dropped = [ChatCompletionUserMessageParam(role="user", content="older turn")]

    assert replace_dropped_with_summary(messages, dropped, _summary_dispatch()) is True

    # Summary is inserted after the system message
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are an agent"
    assert messages[1]["role"] == "user"
    assert _SUMMARY_HEADER in messages[1]["content"]
    assert messages[2]["content"] == "still here"
    assert len(messages) == 3


def test_replace_dropped_inserts_first_without_system_prompt() -> None:
    """With no system prompt the summary becomes the opening message."""
    messages = [ChatCompletionUserMessageParam(role="user", content="still here")]
    dropped = [ChatCompletionUserMessageParam(role="user", content="older turn")]

    replace_dropped_with_summary(messages, dropped, _summary_dispatch())

    assert messages[0]["content"].startswith(_SUMMARY_HEADER)
    assert len(messages) == 2


def test_replace_dropped_folds_previous_summary_into_the_new_one() -> None:
    """Repeated compactions keep one summary, not one per run."""
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="You are an agent"),
        summary_message("Earlier: user set up the MCP server."),
        ChatCompletionUserMessageParam(role="user", content="still here"),
    ]
    dispatch = _summary_dispatch("Everything so far, including the MCP setup.")

    replace_dropped_with_summary(
        messages,
        [ChatCompletionUserMessageParam(role="user", content="older turn")],
        dispatch,
    )

    # The previous separate summary message is removed and replaced with a new one
    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are an agent"
    assert messages[1]["role"] == "user"
    assert _SUMMARY_HEADER in messages[1]["content"]
    assert "Everything so far" in messages[1]["content"]
    assert messages[2]["content"] == "still here"

    # ...and the old one was fed back in, so its content is not lost.
    transcript = dispatch.call_args[0][0]["messages"][1]["content"]
    assert "Earlier: user set up the MCP server." in transcript
    assert "older turn" in transcript


def test_replace_dropped_leaves_messages_alone_on_empty_summary() -> None:
    """No summary means no insertion, and any previous summary stays put."""
    previous = summary_message("Earlier work.")
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="You are an agent"),
        previous,
    ]
    dispatch = Mock()
    dispatch.return_value.choices = []

    assert replace_dropped_with_summary(messages, [{"role": "user", "content": "x"}], dispatch) is False
    assert messages == [
        ChatCompletionSystemMessageParam(role="system", content="You are an agent"),
        previous,
    ]


def test_replace_dropped_skips_the_call_when_nothing_was_dropped() -> None:
    """Tier 1 alone dropped no turns, so there is nothing to summarize."""
    dispatch = Mock()
    messages = [ChatCompletionUserMessageParam(role="user", content="still here")]

    assert replace_dropped_with_summary(messages, [], dispatch) is False
    dispatch.assert_not_called()
    assert len(messages) == 1
