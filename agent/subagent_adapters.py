"""Concrete adapter implementations for sub-agents.

These adapters wrap sub-agent-specific logic (scoped registry, no streaming,
no compaction) to make them compatible with the ReActEngine.

Both adapters also report what they see to the caller — each LLM response, each
batch of tool calls — which is how a live display learns what a sub-agent is
doing while it runs. See agent.subagents.progress.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCall,
)

from agent.engine.types import ToolOutcome
from agent.loop_utils import execute_tool_calls
from agent.tools import ToolRegistry


async def _call_dispatch(dispatch: Callable[[dict], Any], request: dict) -> ChatCompletion:
    """Call a dispatcher that may be sync or async, without blocking the loop.

    The production dispatcher (agent.llm.dispatcher.make_chat_completions_effect)
    is a blocking function; test doubles are coroutines. Awaiting the blocking
    one is a TypeError, and calling it inline would pin the event loop for the
    whole request — which would quietly serialize delegate_parallel, since every
    sub-agent would take its turn on the one thread. Off-thread execution keeps
    parallel sub-agents actually parallel.
    """
    if inspect.iscoroutinefunction(dispatch):
        return await dispatch(request)

    result = await asyncio.to_thread(dispatch, request)
    # A sync callable that returns an awaitable (a bare AsyncMock, a partial
    # wrapping a coroutine function) still has to be awaited.
    if inspect.isawaitable(result):
        return await result
    return result


class SubAgentLLMAdapter:
    """LLM adapter for sub-agents (non-streaming only)."""

    def __init__(
        self,
        dispatch: Callable[[dict], ChatCompletion],
        on_response: Callable[[ChatCompletion], None] | None = None,
    ):
        """Initialize sub-agent LLM adapter.

        Args:
            dispatch: Dispatch callable, sync or async
            on_response: Optional sink receiving each completion, for token
                         accounting
        """
        self.dispatch = dispatch
        self.on_response = on_response

    async def call(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict],
    ) -> ChatCompletion:
        """Call LLM with messages and tools (non-streaming)."""
        request = {
            "messages": messages,
            "tools": tools,
        }
        response = await _call_dispatch(self.dispatch, request)
        if self.on_response is not None:
            self.on_response(response)
        return response


class SubAgentToolAdapter:
    """Tool adapter for sub-agents (scoped registry + permissions)."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_module,
        on_tool_calls: Callable[[list[str]], None] | None = None,
    ):
        """Initialize sub-agent tool adapter.

        Args:
            registry: ToolRegistry (typically scoped)
            permission_module: PermissionModule with is_subagent=True
            on_tool_calls: Optional sink receiving the tool names of each batch
                           before it is dispatched, for progress reporting
        """
        self.registry = registry
        self.permission_module = permission_module
        self.on_tool_calls = on_tool_calls

    def get_schemas(self) -> list[dict]:
        """Get tool schemas from scoped registry."""
        return self.registry.get_schemas()

    async def dispatch_all(
        self,
        tool_calls: list[ChatCompletionMessageToolCall],
    ) -> list[ToolOutcome]:
        """Execute all tool calls with sub-agent permissions.

        Note: Sub-agents have no confirmation_fn (ASK policies are auto-denied).
        """
        if self.on_tool_calls is not None:
            self.on_tool_calls([tc.function.name for tc in tool_calls])

        return await execute_tool_calls(
            tool_calls=tool_calls,
            registry=self.registry,
            on_trace=None,  # Sub-agent steps are summarized by its card, not traced
            permission_module=self.permission_module,
            confirmation_fn=None,  # No confirmation channel for sub-agents
        )


class SubAgentContextAdapter:
    """Context adapter for sub-agents (no compaction)."""

    def compact(
        self,
        messages: list[ChatCompletionMessageParam],
        budget: int,
    ) -> list[ChatCompletionMessageParam]:
        """Sub-agents don't compact context (pass-through)."""
        return messages

    def estimate_tokens(
        self,
        messages: list[ChatCompletionMessageParam],
    ) -> int:
        """Rough token estimation (not used by sub-agents)."""
        return len(messages) * 100
