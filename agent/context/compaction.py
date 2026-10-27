"""Context compaction strategies to keep conversations within budget.

Implements a three-tier compaction strategy (per harness-extension-plan.md):

Tier 1: Truncate oversized Observations to max_observation_chars, marked [truncated]
    - Free (no LLM call)
    - Recovers budget from large file reads
    - Applied at the Observation level where text originates

Tier 2: Drop oldest complete Turns, never splitting tool_calls groups
    - Free (no LLM call)
    - Removes old context entirely
    - Preserves the pairing invariant: assistant message with tool_calls
      and its tool replies are one indivisible unit

Tier 3: Summarize the Turns tier 2 dropped, replacing them with one recap message
    - Requires an LLM call, so it runs only when tier 2 actually dropped something
    - Keeps the facts later turns depend on: paths, decisions, error text
    - Reasoning is disabled for that call; see _SUMMARY_REASONING_EFFORT

The pairing invariant is critical: an assistant message with tool_calls and
its corresponding role="tool" replies must always stay together or be dropped
together. Breaking this causes 400 errors from the API.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

_SUMMARY_HEADER = "[Summary of earlier conversation, compacted to fit the context window]"
"""Prefix on the inserted summary, so the model can tell recap from live conversation."""


class SummaryTruncated(RuntimeError):
    """The summarization call ended before the model wrote any summary text."""


_SUMMARY_MAX_TOKENS = 1500
"""Completion cap for the summarization call.

Generous because the cost of being wrong is asymmetric: an over-large cap wastes nothing
(the model stops when done), while too small a one returns no summary at all and the turns
are lost.
"""

_SUMMARY_REASONING_EFFORT = "none"
"""Reasoning budget for the summarization call.

Condensing a transcript needs no chain-of-thought, and on the served Qwen model the
reasoning runs *before* any answer text — so with reasoning on, the completion cap is spent
thinking and the call returns finish_reason="length" with content=None. Measured on one
6-turn transcript: 1016 completion tokens with reasoning, 81 without, same summary.

Set context.summary_reasoning_effort to "" for a provider that rejects the parameter.
"""

_TRANSCRIPT_CHARS_PER_MESSAGE = 2000
"""Per-message cap when rendering dropped turns for the summarizer.

The transcript is itself a prompt, so it has to fit the window the compaction is trying to
free. Dropped turns can carry huge observations, hence the cap.
"""


@lru_cache(maxsize=1)
def load_summary_prompt() -> str:
    """Load the summarization instruction from agent/prompts/internal/compaction.md.

    Kept in the prompts tree with the Agent Mode prompts, so prompt wording is edited in one
    place rather than in Python. It sits in internal/ because available_modes() globs
    prompts/*.md — a file directly in that directory would be offered as a switchable mode.
    """
    prompt_file = Path(__file__).resolve().parent.parent / "prompts" / "internal" / "compaction.md"
    return prompt_file.read_text(encoding="utf-8").strip()


_TRUNCATION_MARKER = "\n\n[truncated: showing first {kept} characters]"
"""Tail appended to a shortened observation, so the model sees that content is missing."""


def truncate_observation(content: str, max_chars: int) -> str:
    """Truncate observation content if it exceeds max_chars.

    Args:
        content: The observation text to potentially truncate
        max_chars: Maximum character limit

    Returns:
        Original content if under limit, or truncated content with [truncated] marker.
        The result is always within max_chars, marker included.
    """
    if len(content) <= max_chars:
        return content

    # The marker counts against max_chars so that re-truncating an already-truncated
    # observation is a no-op. Appending it on top of a full-width slice would leave every
    # result over the limit, so each later pass would shave off another marker's worth of
    # content and report a truncation that freed nothing.
    #
    # Sizing the reservation with max_chars rather than the final count is safe: kept is
    # never larger, so its rendered digits are never wider.
    reserved = len(_TRUNCATION_MARKER.format(kept=max_chars))
    kept = max(0, max_chars - reserved)
    return content[:kept] + _TRUNCATION_MARKER.format(kept=kept)


def compact_tier1_truncate_observations(
    messages: list[ChatCompletionMessageParam],
    max_observation_chars: int,
) -> int:
    """Tier 1: Truncate oversized tool observations in place.

    Mutates the messages list directly, truncating any tool message content
    that exceeds max_observation_chars.

    Args:
        messages: List of messages to compact (mutated in place)
        max_observation_chars: Maximum characters per observation

    Returns:
        Number of observations truncated
    """
    truncated_count = 0

    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content")
            if content and isinstance(content, str) and len(content) > max_observation_chars:
                msg["content"] = truncate_observation(content, max_observation_chars)  # type: ignore
                truncated_count += 1

    return truncated_count


def find_turn_boundaries(messages: list[ChatCompletionMessageParam]) -> list[tuple[int, int]]:
    """Find Turn boundaries in the message list.

    A Turn is either:
    - A single user message
    - A single system message
    - An assistant message with tool_calls + its tool responses (indivisible group)
    - An assistant message without tool_calls

    Returns:
        List of (start_idx, end_idx) tuples for each turn (inclusive)
    """
    turns: list[tuple[int, int]] = []
    i = 0

    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")

        if role == "system":
            # System message is its own turn
            turns.append((i, i))
            i += 1
        elif role == "user":
            # User message is its own turn
            turns.append((i, i))
            i += 1
        elif role == "assistant":
            # Assistant message - check if it has tool_calls
            if msg.get("tool_calls"):
                # This is a tool-calling turn - need to find all corresponding tool responses
                start = i
                i += 1  # Move past the assistant message

                # Collect all immediately following tool messages
                while i < len(messages) and messages[i].get("role") == "tool":
                    i += 1

                # Turn spans from assistant message to last tool response
                turns.append((start, i - 1))
            else:
                # Assistant message without tools - standalone turn
                turns.append((i, i))
                i += 1
        elif role == "tool":
            # Orphaned tool message (shouldn't happen if pairing is correct)
            # Treat as standalone turn
            turns.append((i, i))
            i += 1
        else:
            # Unknown role - treat as standalone
            turns.append((i, i))
            i += 1

    return turns


def compact_tier2_drop_turns(
    messages: list[ChatCompletionMessageParam],
    target_tokens: int,
    estimate_fn: callable,
) -> tuple[int, list[ChatCompletionMessageParam]]:
    """Tier 2: Drop oldest complete Turns to fit within budget.

    Never splits assistant+tool groups. Preserves:
    - Index 0 (system message if present)
    - Most recent user message (last user turn)

    Args:
        messages: List of messages (will be mutated)
        target_tokens: Target token budget to fit within
        estimate_fn: Function to estimate token count (messages) -> int

    Returns:
        Tuple of (turns_dropped_count, dropped_messages)
    """
    # Don't drop anything if already under budget
    current_tokens = estimate_fn(messages)
    if current_tokens <= target_tokens:
        return (0, [])

    # Find turn boundaries
    turns = find_turn_boundaries(messages)
    if not turns:
        return (0, [])

    # Identify protected indices
    protected_indices = set()

    # Protect index 0 (system message if present)
    if messages and messages[0].get("role") == "system":
        protected_indices.add(0)

    # Protect the most recent user message
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            protected_indices.add(i)
            break

    # Find droppable turns (those that don't contain protected indices)
    droppable_turns = []
    for start, end in turns:
        # Check if this turn contains any protected index
        turn_range = set(range(start, end + 1))
        if not turn_range.intersection(protected_indices):
            droppable_turns.append((start, end))

    # Drop oldest turns first until we fit the budget
    turns_dropped = 0
    dropped_messages: list[ChatCompletionMessageParam] = []

    # Track cumulative offset as we delete messages
    offset = 0

    for start, end in droppable_turns:
        # Adjust indices for previous deletions
        adjusted_start = start - offset
        adjusted_end = end - offset

        # Try dropping this turn
        turn_messages = messages[adjusted_start : adjusted_end + 1]
        dropped_messages.extend(turn_messages)

        # Remove from messages list
        turn_size = adjusted_end - adjusted_start + 1
        for _ in range(turn_size):
            del messages[adjusted_start]

        # Update offset for next iteration
        offset += turn_size
        turns_dropped += 1

        # Check if we're now under budget
        current_tokens = estimate_fn(messages)
        if current_tokens <= target_tokens:
            break

    return (turns_dropped, dropped_messages)


def render_transcript(messages: list[ChatCompletionMessageParam]) -> str:
    """Render messages as a plain transcript for the summarizer to read.

    Tool calls are rendered by name rather than by their full argument JSON: the summary
    needs to record that a tool ran and what it returned, and the arguments are usually
    recoverable from the observation.
    """
    lines = []
    for msg in messages:
        role = str(msg.get("role", "?"))
        content = str(msg.get("content") or "")
        if len(content) > _TRANSCRIPT_CHARS_PER_MESSAGE:
            content = content[:_TRANSCRIPT_CHARS_PER_MESSAGE] + " …"

        called = []
        for call in msg.get("tool_calls") or []:
            data = call.model_dump() if hasattr(call, "model_dump") else call
            called.append(str(data.get("function", {}).get("name", "?")))
        if called:
            content = f"{content} (called: {', '.join(called)})".strip()

        if content:
            lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def summarize_messages(
    messages: list[ChatCompletionMessageParam],
    dispatch: Callable[[Any], Any],
    max_tokens: int = _SUMMARY_MAX_TOKENS,
    instructions: str = "",
    reasoning_effort: str = _SUMMARY_REASONING_EFFORT,
) -> str:
    """Summarize dropped turns into a single paragraph of continuation notes.

    Args:
        messages: The turns being dropped.
        dispatch: Non-streaming completion callable, as built by
            make_chat_completions_effect. Model and timeout come from its config.
        max_tokens: Cap on the summary, so replacing the dropped turns is a clear net win.
        instructions: Optional caller steer, appended to the prompt. Only the user can say
            which of several equally summarizable threads still matters to them, so /compact
            passes its argument through here.

    Returns:
        The summary text, or "" if the model returned nothing usable. An empty return means
        the caller should keep the plain drop rather than insert a placeholder.
    """
    if not messages:
        return ""

    prompt = load_summary_prompt()
    if instructions:
        prompt = f"{prompt}\n\nThe user asked you to focus on this in particular:\n{instructions}"

    request: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": render_transcript(messages)},
        ],
        "max_tokens": max_tokens,
    }
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort

    response = dispatch(request)

    choices = getattr(response, "choices", None)
    if not choices:
        return ""

    choice = choices[0]
    content = (choice.message.content or "").strip()
    if content:
        return content

    # A reasoning model that runs out of budget mid-thought returns no content at all.
    # Degrading quietly here would report a plain drop and leave no trace of why, so the
    # cause is raised for the caller to surface.
    if getattr(choice, "finish_reason", None) == "length":
        raise SummaryTruncated(
            f"the model hit the {max_tokens}-token cap before writing any summary "
            f"(it spends the cap on reasoning first) — raise context.summary_max_tokens"
        )
    return ""


def find_summary_index(messages: list[ChatCompletionMessageParam]) -> int | None:
    """Locate an existing compaction summary, or None if the conversation has none.

    Repeated compactions fold into a single summary rather than stacking one per run: the
    caller feeds the old summary back to the summarizer and replaces it, so nothing that
    was already condensed is lost the second time round.
    """
    for index, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, str) and content.startswith(_SUMMARY_HEADER):
            return index
    return None


def summary_message(summary: str) -> ChatCompletionMessageParam:
    """Wrap summary text as the user message that stands in for the dropped turns."""
    return {"role": "user", "content": f"{_SUMMARY_HEADER}\n\n{summary}"}  # type: ignore[return-value]


def summary_text(messages: list[ChatCompletionMessageParam]) -> str:
    """Return the conversation's summary without its header, or "" if it has none.

    replace_dropped_with_summary reports only whether it inserted a summary, because that is
    all the conversation needs. Promotion to long-term memory needs the text itself, and it
    must be the header-free text: the header explains the message's role inside a context
    window, which is meaningless once the string is a stored memory.
    """
    index = find_summary_index(messages)
    if index is None:
        return ""
    content = messages[index].get("content")
    if not isinstance(content, str):
        return ""
    return content[len(_SUMMARY_HEADER) :].strip()


def replace_dropped_with_summary(
    messages: list[ChatCompletionMessageParam],
    dropped: list[ChatCompletionMessageParam],
    dispatch: Callable[[Any], Any],
    instructions: str = "",
    max_tokens: int = _SUMMARY_MAX_TOKENS,
    reasoning_effort: str = _SUMMARY_REASONING_EFFORT,
) -> bool:
    """Insert a summary of the dropped turns into the surviving conversation.

    Shared by /compact and the loop's automatic compaction so the two cannot drift: both
    fold any earlier summary into the new one, and both append the summary to the existing
    system message to ensure system messages remain at position 0 only (required by some models).

    Args:
        messages: The compacted conversation, mutated in place.
        dropped: The turns compact() removed.
        dispatch: Non-streaming completion callable.
        instructions: Optional steer forwarded to the summarizer.

    Returns:
        True if a summary was inserted. False means the summarizer returned nothing and the
        caller should report a plain drop.

    Raises whatever dispatch raises — the caller decides how a failed summary is reported.
    """
    if not dropped:
        return False

    # An earlier summary is folded into the new one rather than left beside it, so repeated
    # compactions keep condensing a single message instead of stacking one per run.
    previous = find_summary_index(messages)
    to_summarize = ([messages[previous]] if previous is not None else []) + dropped

    summary = summarize_messages(
        to_summarize,
        dispatch,
        max_tokens=max_tokens,
        instructions=instructions,
        reasoning_effort=reasoning_effort,
    )
    if not summary:
        return False

    if previous is not None:
        messages.pop(previous)

    # Insert summary as a user message after any system message at position 0.
    # This avoids creating multiple system messages which some models reject.
    summary_msg = summary_message(summary)
    
    # Find insertion point: after system message if present, otherwise at position 0
    insert_pos = 0
    if messages and messages[0].get("role") == "system":
        insert_pos = 1
    
    messages.insert(insert_pos, summary_msg)
    
    return True


def compact(
    messages: list[ChatCompletionMessageParam],
    target_tokens: int,
    estimate_fn: callable,
    max_observation_chars: int = 10000,
) -> dict[str, Any]:
    """Apply compaction strategy to fit messages within token budget.

    Applies tiers in order: truncate observations, then drop old turns.
    Mutates the messages list in place.

    Args:
        messages: Message list to compact (mutated in place)
        target_tokens: Target token budget
        estimate_fn: Function to estimate tokens: (messages) -> int
        max_observation_chars: Max chars for tier 1 truncation

    Returns:
        Dictionary with compaction stats:
            - observations_truncated: count of truncated tool messages
            - turns_dropped: count of complete turns removed
            - final_tokens: token count after compaction
            - dropped_messages: list of messages that were dropped
    """
    # Tier 1: Truncate oversized observations
    observations_truncated = compact_tier1_truncate_observations(
        messages,
        max_observation_chars,
    )

    # Check if tier 1 was enough
    current_tokens = estimate_fn(messages)
    if current_tokens <= target_tokens:
        return {
            "observations_truncated": observations_truncated,
            "turns_dropped": 0,
            "final_tokens": current_tokens,
            "dropped_messages": [],
        }

    # Tier 2: Drop oldest turns
    turns_dropped, dropped_messages = compact_tier2_drop_turns(
        messages,
        target_tokens,
        estimate_fn,
    )

    final_tokens = estimate_fn(messages)

    return {
        "observations_truncated": observations_truncated,
        "turns_dropped": turns_dropped,
        "final_tokens": final_tokens,
        "dropped_messages": dropped_messages,
    }
