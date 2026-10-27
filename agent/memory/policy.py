"""Memory write/read policies and decision rules.

This module defines when and what to store, and when and how to retrieve.
The policy is explicitly stated here so it can be tested, documented, and
pointed at.

Write Policy:
- Explicit: user calls remember() tool
- Automatic: compaction writes its summary before discarding turns
- Guards: reject empty content, reject error strings, replace (don't append) summaries

Read Policy:
- At startup: inject pinned memories via rules mechanism (max 10 rows / 2000 chars)
- Every turn: retrieve top 3 memories for the user's message
- On demand: user calls search_memory() tool

What is NEVER stored:
- Credentials (API keys, tokens, passwords)
- Transient state (current working directory, open file handles)
- Raw LLM responses (only validated summaries from compaction)
"""

from __future__ import annotations

# Startup injection limits (applied to pinned memories)
PINNED_MAX_COUNT = 10
PINNED_MAX_CHARS = 2000

# Per-turn retrieval limit
PER_TURN_RETRIEVAL_LIMIT = 3

# On-demand search default limit
SEARCH_DEFAULT_LIMIT = 3
SEARCH_MAX_LIMIT = 20


def should_store_content(content: str) -> tuple[bool, str]:
    """Determine if content is valid for storage.
    
    Args:
        content: Content to validate
        
    Returns:
        Tuple of (should_store, reason)
        - (True, "") if content should be stored
        - (False, reason) if content should be rejected
    """
    if not content:
        return False, "Content is empty"
    
    if not content.strip():
        return False, "Content is whitespace only"
    
    if content.startswith("Error:"):
        return False, "Content is an error string"
    
    return True, ""


def format_pinned_injection(pinned_memories: list, stats: dict[str, int]) -> str:
    """Format pinned memories and stats for system prompt injection.
    
    Args:
        pinned_memories: List of Memory objects to inject
        stats: Memory statistics dict (total, fact_count, etc.)
        
    Returns:
        Formatted section for system prompt
    """
    if not pinned_memories and stats["total"] == 0:
        return ""
    
    lines = ["\n---\n", "## Long-term Memory\n"]
    
    if pinned_memories:
        lines.append("\n### Pinned Memories\n")
        for memory in pinned_memories:
            kind_label = memory.kind.capitalize()
            lines.append(f"\n**{kind_label}**: {memory.content}\n")
    
    # Add availability hint
    if stats["total"] > 0:
        lines.append(f"\n*{stats['total']} memories available")
        kinds = []
        if stats["fact_count"] > 0:
            kinds.append(f"{stats['fact_count']} facts")
        if stats["preference_count"] > 0:
            kinds.append(f"{stats['preference_count']} preferences")
        if stats["summary_count"] > 0:
            kinds.append(f"{stats['summary_count']} summaries")
        if kinds:
            lines.append(f" ({', '.join(kinds)})")
        lines.append(". Use search_memory() to retrieve specific information.*\n")
    
    return "".join(lines)


def format_per_turn_retrieval(memories: list) -> str:
    """Format memories retrieved for the current turn.
    
    Args:
        memories: List of Memory objects to inject
        
    Returns:
        Formatted section to prepend to user message or inject as system
    """
    if not memories:
        return ""
    
    lines = ["**Relevant memories:**\n"]
    for memory in memories:
        lines.append(f"- {memory.content}\n")
    lines.append("\n")
    
    return "".join(lines)

