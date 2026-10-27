"""Token estimation for context budget management.

Token estimation uses a character-per-token heuristic rather than an exact tokenizer
because:
1. The served models (Qwen/Gemma over InnKube) do not expose their exact tokenizer
2. A conservative heuristic with headroom is more reliable than false precision
3. The estimate includes tool schemas, which are re-sent every turn and grow with MCP

The default chars_per_token ratio of 3.5 is deliberately conservative for the
target models.
"""

from __future__ import annotations

import json
from typing import Any

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam


def estimate_tokens(
    messages: list[ChatCompletionMessageParam],
    schemas: list[ChatCompletionToolParam] | None = None,
    chars_per_token: float = 3.5,
    per_message_overhead: int = 4,
) -> int:
    """Estimate token count for messages and tool schemas.

    Uses a character-based heuristic rather than exact tokenization because the
    served LLMs do not expose their tokenizer over this endpoint.

    Args:
        messages: List of chat completion messages (system, user, assistant, tool)
        schemas: Optional list of tool schemas (included in every turn)
        chars_per_token: Character-to-token ratio (default 3.5, conservative for Qwen/Gemma)
        per_message_overhead: Fixed token overhead per message (default 4)

    Returns:
        Estimated token count for the entire request

    Implementation notes:
        - Counts all message content: role, content, tool_calls, tool_call_id
        - Includes serialized tool schemas (sent on every turn)
        - Conservative ratio ensures we stay within budget
        - Does NOT include response tokens (those are reserved separately)
    """
    total_chars = 0

    # Count message content
    for msg in messages:
        # Role field (always present)
        total_chars += len(msg.get("role", ""))

        # Content field (may be None or string)
        content = msg.get("content")
        if content:
            total_chars += len(str(content))

        # Tool calls (assistant messages with tools)
        if msg.get("tool_calls"):
            tool_calls = msg["tool_calls"]
            if tool_calls:
                # Convert tool calls to dicts for serialization
                # tool_calls can be list of Pydantic models or dicts
                tool_calls_data = []
                for tc in tool_calls:
                    if hasattr(tc, "model_dump"):
                        # Pydantic model with model_dump method
                        tool_calls_data.append(tc.model_dump())
                    else:
                        # Already a dict
                        tool_calls_data.append(tc)
                total_chars += len(json.dumps(tool_calls_data))

        # Tool call ID (tool messages)
        if "tool_call_id" in msg:
            total_chars += len(str(msg["tool_call_id"]))

        # Name field (function/tool messages)
        if "name" in msg:
            total_chars += len(str(msg["name"]))

    # Count tool schemas
    if schemas:
        # Schemas are sent as JSON, so serialize to get accurate size
        total_chars += len(json.dumps(schemas))

    # Convert chars to tokens using the heuristic
    estimated_tokens = int(total_chars / chars_per_token)

    # Add per-message overhead
    message_overhead = len(messages) * per_message_overhead

    return estimated_tokens + message_overhead


def is_within_budget(
    messages: list[ChatCompletionMessageParam],
    schemas: list[ChatCompletionToolParam] | None,
    config: dict[str, Any],
) -> bool:
    """Check if the current context fits within the configured budget.

    Args:
        messages: Current conversation history
        schemas: Tool schemas (if any)
        config: Configuration dict with 'context' section

    Returns:
        True if context fits within window_tokens - reserve_output_tokens
    """
    context_config = config.get("context", {})
    window_tokens = context_config.get("window_tokens", 8192)
    reserve_output = context_config.get("reserve_output_tokens", 2048)
    chars_per_token = context_config.get("chars_per_token", 3.5)

    current_tokens = estimate_tokens(
        messages,
        schemas,
        chars_per_token=chars_per_token,
    )

    available_budget = window_tokens - reserve_output
    return current_tokens <= available_budget


def get_budget_info(
    messages: list[ChatCompletionMessageParam],
    schemas: list[ChatCompletionToolParam] | None,
    config: dict[str, Any],
) -> dict[str, int]:
    """Get detailed budget breakdown for /context command.

    Args:
        messages: Current conversation history
        schemas: Tool schemas (if any)
        config: Configuration dict with 'context' section

    Returns:
        Dictionary with budget details:
            - current_tokens: estimated tokens used
            - window_tokens: total window size
            - reserve_output: tokens reserved for response
            - available: tokens available for input
            - remaining: tokens still available
            - utilization_pct: percentage of available budget used
    """
    context_config = config.get("context", {})
    window_tokens = context_config.get("window_tokens", 8192)
    reserve_output = context_config.get("reserve_output_tokens", 2048)
    chars_per_token = context_config.get("chars_per_token", 3.5)

    current_tokens = estimate_tokens(
        messages,
        schemas,
        chars_per_token=chars_per_token,
    )

    available = window_tokens - reserve_output
    remaining = available - current_tokens
    utilization_pct = int((current_tokens / available) * 100) if available > 0 else 0

    return {
        "current_tokens": current_tokens,
        "window_tokens": window_tokens,
        "reserve_output": reserve_output,
        "available": available,
        "remaining": remaining,
        "utilization_pct": utilization_pct,
    }
