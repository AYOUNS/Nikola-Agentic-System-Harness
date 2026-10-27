"""Concrete adapter implementations for the main loop.

These adapters wrap the existing dispatch functions, registry, and compaction
logic to make them compatible with the ReActEngine.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCall,
)

from agent.context import (
    compact,
    estimate_tokens,
    replace_dropped_with_summary,
    summary_text,
)
from agent.engine.types import ToolOutcome
from agent.loop_utils import accumulate_stream, execute_tool_calls
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MainLoopLLMAdapter:
    """LLM adapter for main loop (supports streaming)."""
    
    def __init__(
        self,
        dispatch: Callable[[dict], ChatCompletion],
        stream_dispatch: Callable[[dict], Iterator[ChatCompletionChunk]] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ):
        """Initialize LLM adapter.
        
        Args:
            dispatch: Non-streaming dispatch callable
            stream_dispatch: Optional streaming dispatch callable
            on_token: Optional callback for streamed tokens
            on_reasoning: Optional callback for reasoning tokens
        """
        self.dispatch = dispatch
        self.stream_dispatch = stream_dispatch
        self.on_token = on_token
        self.on_reasoning = on_reasoning
    
    async def call(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict],
    ) -> ChatCompletion:
        """Call LLM with messages and tools.
        
        Uses streaming if available, otherwise non-streaming.
        """
        request = {
            "messages": messages,
            "tools": tools,
        }
        
        # Use streaming if available
        if self.stream_dispatch is not None:
            message, finish_reason = accumulate_stream(
                self.stream_dispatch(request),
                on_token=self.on_token,
                on_reasoning=self.on_reasoning,
            )
            
            # Build ChatCompletion from accumulated message
            # Note: accumulate_stream returns a ChatCompletionMessage
            from openai.types.chat.chat_completion import Choice, CompletionUsage
            
            return ChatCompletion(
                id="stream",
                model="unknown",
                object="chat.completion",
                created=0,
                choices=[Choice(
                    index=0,
                    message=message,
                    finish_reason=finish_reason or "stop",
                )],
                usage=CompletionUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            )
        else:
            # Use non-streaming dispatch
            return self.dispatch(request)


class MainLoopToolAdapter:
    """Tool adapter for main loop (includes permissions)."""
    
    def __init__(
        self,
        registry: ToolRegistry,
        permission_module=None,
        confirmation_fn=None,
        on_trace: Callable[[str], None] | None = None,
    ):
        """Initialize tool adapter.
        
        Args:
            registry: ToolRegistry for tool dispatch
            permission_module: Optional PermissionModule for checks
            confirmation_fn: Optional callback for user confirmation
            on_trace: Optional callback for tool trace lines
        """
        self.registry = registry
        self.permission_module = permission_module
        self.confirmation_fn = confirmation_fn
        self.on_trace = on_trace
    
    def get_schemas(self) -> list[dict]:
        """Get tool schemas from registry."""
        return self.registry.get_schemas()
    
    async def dispatch_all(
        self,
        tool_calls: list[ChatCompletionMessageToolCall],
    ) -> list[ToolOutcome]:
        """Execute all tool calls with permissions."""
        return await execute_tool_calls(
            tool_calls=tool_calls,
            registry=self.registry,
            on_trace=self.on_trace,
            permission_module=self.permission_module,
            confirmation_fn=self.confirmation_fn,
        )


class MainLoopContextAdapter:
    """Context adapter for main loop (compaction)."""

    def __init__(
        self,
        config: dict[str, Any],
        dispatch: Callable[[dict], ChatCompletion] | None = None,
        on_summary: Callable[[str], None] | None = None,
    ):
        """Initialize context adapter.

        Args:
            config: Application config with compaction settings
            dispatch: Non-streaming dispatch used for tier 3 summarization. Without it
                compaction stops after tier 2 and the dropped turns are simply gone, which
                is the correct degradation for a caller that has no model to call.
            on_summary: Called with the summary text once tier 3 produces one, so the
                summary can be promoted to long-term memory. The adapter stays unaware of
                the memory store: it produces a string and hands it over.
        """
        self.config = config
        self.dispatch = dispatch
        self.on_summary = on_summary

    def compact(
        self,
        messages: list[ChatCompletionMessageParam],
        budget: int,
    ) -> list[ChatCompletionMessageParam]:
        """Compact messages using main loop strategy."""
        # compact() needs the same estimator the engine used to decide it was over
        # budget, or it would compact against a different scale than it was asked to.
        # It mutates messages in place and returns stats, so return the list itself.
        context_config = self.config.get("context", {})
        stats = compact(
            messages,
            budget,
            estimate_tokens,
            max_observation_chars=context_config.get("max_observation_chars", 10000),
        )

        dropped = stats.get("dropped_messages") or []
        if dropped and self.dispatch is not None:
            self._summarize_dropped(messages, dropped, context_config)

        return messages

    def _summarize_dropped(
        self,
        messages: list[ChatCompletionMessageParam],
        dropped: list[ChatCompletionMessageParam],
        context_config: dict[str, Any],
    ) -> None:
        """Run tier 3 over the turns tier 2 dropped, and promote the recap.

        Every failure here is logged and swallowed. A summary that cannot be written is a
        lost recap; an exception raised into the engine mid-turn is a lost turn, and the
        turn is worth more than the recap.
        """
        try:
            inserted = replace_dropped_with_summary(
                messages,
                dropped,
                self.dispatch,
                max_tokens=context_config.get("summary_max_tokens", 1500),
                reasoning_effort=context_config.get("summary_reasoning_effort", "none"),
            )
        except Exception as error:
            logger.warning(
                f"Tier 3 summarization failed ({error}); {len(dropped)} dropped message(s) "
                "were not recapped."
            )
            return

        if not inserted:
            logger.info("Summarizer returned nothing; reporting a plain drop.")
            return

        if self.on_summary is not None:
            # summary_text re-reads what was inserted rather than trusting a second call to
            # the model: the promoted memory and the in-window recap must be the same text.
            text = summary_text(messages)
            if text:
                self.on_summary(text)
    
    def estimate_tokens(
        self,
        messages: list[ChatCompletionMessageParam],
    ) -> int:
        """Estimate token count for messages."""
        return estimate_tokens(messages)
