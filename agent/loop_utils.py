"""ReAct loop utilities for tool execution and streaming.

This module provides low-level utilities used by the ReActEngine and adapters:
- Tool argument parsing and execution
- Permission checking integration
- Stream accumulation for OpenAI streaming responses

The main loop logic has been moved to agent.engine.core (ReActEngine).
For the high-level turn execution interface, see agent.loop.make_process_turn_fn_v2.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionToolMessageParam,
)
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from agent.engine.types import ToolOutcome
from agent.tools import ToolRegistry

OutputKind = Literal["answer", "reasoning", "tool"]
"""What a rendered fragment is: the answer itself, the model's thinking, or tool activity."""


def summarize(text: str, limit: int = 90) -> str:
    """Flatten text to a single line, truncated for one-line trace output."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def parse_tool_arguments(raw_arguments: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Parse JSON tool arguments string safely.

    raw_arguments: Raw JSON string from tool_call.function.arguments.

    Returns:
        A tuple of (parsed_dict, None) on success, or (None, error_message) on failure.
    """
    if not raw_arguments or raw_arguments.strip() == "":
        return {}, None
    try:
        parsed = json.loads(raw_arguments)
        if isinstance(parsed, dict):
            return parsed, None
        return None, f"Arguments must be a JSON object, got {type(parsed).__name__}"
    except json.JSONDecodeError as err:
        return None, f"Invalid JSON in tool call arguments: {err}"


async def execute_tool_call(
    tc: ChatCompletionMessageToolCall,
    registry: ToolRegistry,
    permission_module=None,
    confirmation_fn=None,
) -> ToolOutcome:
    """Execute a single tool call against the registry and return a ToolOutcome.

    Catches argument parsing issues and dispatch errors, formatting them as
    error observations. Tracks permission decisions and wall-clock duration so
    the caller can report them without knowing the internals.

    Args:
        tc: Tool call from LLM response
        registry: ToolRegistry for tool dispatch
        permission_module: Optional PermissionModule for permission checking
        confirmation_fn: Optional function(tool_name, args) -> (approved, scope) for ASK actions

    Returns:
        ToolOutcome with the tool message, timing, permission outcome and success flag.
    """
    start_time = time.perf_counter()
    permission_outcome = "none"
    args, parse_err = parse_tool_arguments(tc.function.arguments)
    if parse_err is not None:
        observation = f"Error: {parse_err}"
    else:
        try:
            # Check permission if module provided
            if permission_module is not None:
                from agent.permissions import NO_RETRY_DIRECTIVE, PermissionAction

                decision = permission_module.check(tc.function.name, args or {})

                if decision.action == PermissionAction.DENY:
                    permission_outcome = "deny"
                    observation = f"Error: {decision.error_message}"
                elif decision.action == PermissionAction.ASK:
                    # Need user confirmation
                    if confirmation_fn is None:
                        permission_outcome = "deny"
                        observation = (
                            f"Error: Tool '{tc.function.name}' requires confirmation, but no "
                            f"confirmation channel available. {NO_RETRY_DIRECTIVE}"
                        )
                    else:
                        try:
                            approved, scope = confirmation_fn(tc.function.name, args or {})
                            if approved:
                                permission_outcome = "ask→yes"
                                permission_module.record_decision(tc.function.name, args or {}, True, scope)
                                observation = await registry.dispatch(tc.function.name, args or {})
                            else:
                                permission_outcome = "ask→no"
                                permission_module.record_decision(tc.function.name, args or {}, False, scope)
                                observation = (
                                    f"Error: Tool '{tc.function.name}' was denied by the user. "
                                    f"{NO_RETRY_DIRECTIVE}"
                                )
                        except Exception as err:
                            permission_outcome = "deny"
                            observation = f"Error: Confirmation failed: {err}"
                else:  # ALLOW
                    permission_outcome = "allow"
                    observation = await registry.dispatch(tc.function.name, args or {})
            else:
                observation = await registry.dispatch(tc.function.name, args or {})
        except Exception as err:
            observation = f"Error executing tool '{tc.function.name}': {err}"

    return ToolOutcome(
        message=ChatCompletionToolMessageParam(
            role="tool",
            tool_call_id=tc.id,
            content=observation,
        ),
        tool_name=tc.function.name,
        arguments=args or {},
        permission_outcome=permission_outcome,
        duration_ms=(time.perf_counter() - start_time) * 1000,
        success=not str(observation).startswith("Error"),
    )


async def execute_tool_calls(
    tool_calls: list[ChatCompletionMessageToolCall],
    registry: ToolRegistry,
    on_trace: Callable[[str], None] | None = None,
    permission_module=None,
    confirmation_fn=None,
) -> list[ToolOutcome]:
    """Execute a list of tool calls and return outcomes.

    Args:
        tool_calls: List of tool calls from LLM
        registry: ToolRegistry for tool dispatch
        on_trace: optional sink receiving a line before each call and one after its
                  observation, so a caller can show tool turns as they happen instead of
                  leaving the terminal silent while tools run.
        permission_module: Optional PermissionModule for permission checking
        confirmation_fn: Optional function(tool_name, args) -> (approved, scope) for ASK actions
    """
    outcomes = []
    for tc in tool_calls:
        if on_trace is not None:
            on_trace(f"{tc.function.name}({summarize(tc.function.arguments or '')})\n")
        outcome = await execute_tool_call(tc, registry, permission_module, confirmation_fn)
        if on_trace is not None:
            on_trace(f"  → {summarize(str(outcome.message['content']))}\n")
        outcomes.append(outcome)
    return outcomes


def accumulate_stream(
    chunks: Iterator[ChatCompletionChunk],
    on_token: Callable[[str], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> tuple[ChatCompletionMessage | None, str | None]:
    """Reassemble streamed chunks into a single assistant message.

    Content fragments are concatenated (and forwarded to on_token as they arrive so the
    caller can render them live). Tool-call fragments are grouped by their delta index and
    their id / name / argument pieces concatenated, per the OpenAI streaming spec — the
    same turn may therefore carry both text and tool calls.

    Thinking models stream chain-of-thought in a `reasoning_content` (or `reasoning`)
    delta field that the OpenAI schema does not define; the SDK keeps such fields as model
    extras. Those fragments go to on_reasoning and are deliberately kept out of the
    assembled message — echoing thoughts back as assistant content would corrupt history.

    chunks: iterator of ChatCompletionChunk from a streaming dispatch.
    on_token: optional sink called with each content fragment as it arrives.
    on_reasoning: optional sink called with each thinking fragment as it arrives.

    Returns:
        A tuple of (message, finish_reason), or (None, None) if the stream carried no choices.
    """
    content_parts: list[str] = []
    fragments: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    saw_choice = False

    for chunk in chunks:
        if not chunk.choices:
            continue
        saw_choice = True
        choice = chunk.choices[0]
        if choice.finish_reason:
            finish_reason = choice.finish_reason

        delta = choice.delta
        if delta is None:
            continue

        if delta.content:
            content_parts.append(delta.content)
            if on_token is not None:
                on_token(delta.content)

        thinking = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if thinking and on_reasoning is not None:
            on_reasoning(str(thinking))

        for tc_delta in delta.tool_calls or []:
            fragment = fragments.setdefault(tc_delta.index, {"id": "", "name": "", "arguments": ""})
            if tc_delta.id:
                fragment["id"] = tc_delta.id
            if tc_delta.function is not None:
                if tc_delta.function.name:
                    fragment["name"] += tc_delta.function.name
                if tc_delta.function.arguments:
                    fragment["arguments"] += tc_delta.function.arguments

    if not saw_choice:
        return None, None

    tool_calls = [
        ChatCompletionMessageToolCall(
            id=fragment["id"] or f"call_{index}",
            type="function",
            function=Function(name=fragment["name"], arguments=fragment["arguments"]),
        )
        for index, fragment in sorted(fragments.items())
    ]

    content = "".join(content_parts)
    message = ChatCompletionMessage(
        role="assistant",
        content=content or None,
        tool_calls=tool_calls or None,
    )
    return message, finish_reason
