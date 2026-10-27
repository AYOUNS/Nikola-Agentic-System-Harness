"""Context management and token accounting for the ReAct loop.

This package provides token estimation and context compaction to keep
conversations within the LLM's context window budget.
"""

from agent.context.budget import estimate_tokens, get_budget_info, is_within_budget
from agent.context.compaction import (
    compact,
    find_summary_index,
    replace_dropped_with_summary,
    summarize_messages,
    summary_message,
    summary_text,
)

__all__ = [
    "compact",
    "estimate_tokens",
    "find_summary_index",
    "get_budget_info",
    "is_within_budget",
    "replace_dropped_with_summary",
    "summarize_messages",
    "summary_message",
    "summary_text",
]
