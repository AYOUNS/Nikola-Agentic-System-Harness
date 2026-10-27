"""ReAct loop engine types.

Defines the data structures used by the ReAct engine for configuration
and results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai.types.chat import ChatCompletionToolMessageParam


@dataclass
class TurnLimits:
    """Limits and guards for a single ReAct turn.
    
    Attributes:
        max_iterations: Maximum number of iterations before guard fires
        max_tokens: Optional token budget (None = no limit)
        max_time_seconds: Optional time limit in seconds (None = no limit)
    """
    max_iterations: int
    max_tokens: int | None = None
    max_time_seconds: float | None = None


@dataclass
class TurnResult:
    """Result of executing a ReAct turn.
    
    Attributes:
        answer: Final answer text or guard message
        iterations: Number of iterations executed
        guard_fired: Which guard fired ("max_iterations" | "max_time" | None).
            "max_tokens" is not currently produced — token budget triggers
            context compaction, not a loop termination.
        tool_calls_made: Total number of tool calls executed across all iterations
    """
    answer: str
    iterations: int
    guard_fired: str | None = None
    tool_calls_made: int = 0


@dataclass
class ToolOutcome:
    """One tool call's observation plus what happened on the way to it.

    The engine only needs ``message``; the rest is what the permission check and
    the clock saw, carried on the result so the code that reports it cannot be
    separated from the code that knows it.

    Attributes:
        message: Tool message appended to the conversation
        tool_name: Tool that was called
        arguments: Parsed arguments, empty when they failed to parse
        permission_outcome: "allow", "deny", "ask→yes", "ask→no" or "none"
        duration_ms: Wall time from parse to observation
        success: Whether the tool ran and returned without error
    """

    message: ChatCompletionToolMessageParam
    tool_name: str
    arguments: dict[str, Any]
    permission_outcome: str
    duration_ms: float
    success: bool
