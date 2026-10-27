"""ReAct loop engine adapter protocols.

Defines the interfaces that the engine expects from its dependencies.
Uses Protocol (structural typing) so implementations don't need to inherit.
"""

from __future__ import annotations

from typing import Protocol

from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCall,
)

from agent.engine.types import ToolOutcome


class LLMAdapter(Protocol):
    """Adapter for LLM dispatch.
    
    Hides streaming vs non-streaming, provider details, and token tracking.
    The engine just calls call() and gets back a ChatCompletion.
    """
    
    async def call(
        self,
        messages: list[ChatCompletionMessageParam],
        tools: list[dict],
    ) -> ChatCompletion:
        """Request completion from LLM.
        
        Args:
            messages: Full conversation history
            tools: Tool schemas for LLM function calling
            
        Returns:
            ChatCompletion with assistant message (and optional tool calls)
        """
        ...


class ToolAdapter(Protocol):
    """Adapter for tool dispatch.
    
    Hides registry, permissions, error handling, and observation formatting.
    The engine just passes tool calls and gets back tool messages.
    """
    
    def get_schemas(self) -> list[dict]:
        """Get tool schemas for LLM function calling.
        
        Returns:
            List of tool schemas in OpenAI format
        """
        ...
    
    async def dispatch_all(
        self,
        tool_calls: list[ChatCompletionMessageToolCall],
    ) -> list[ToolOutcome]:
        """Execute all tool calls and return outcomes.

        Args:
            tool_calls: Tool calls from LLM response

        Returns:
            List of ToolOutcome (one per tool call), each carrying the message
            to append plus timing and permission data for instrumentation.
        """
        ...


class ContextAdapter(Protocol):
    """Adapter for context management.
    
    Hides compaction strategy, token estimation, and summarization.
    The engine just asks for compacted messages when needed.
    """
    
    def compact(
        self,
        messages: list[ChatCompletionMessageParam],
        budget: int,
    ) -> list[ChatCompletionMessageParam]:
        """Compact messages to fit within token budget.
        
        Args:
            messages: Current message history
            budget: Target token budget
            
        Returns:
            Compacted message list (may be same list if under budget)
        """
        ...
    
    def estimate_tokens(
        self,
        messages: list[ChatCompletionMessageParam],
    ) -> int:
        """Estimate token count for message list.
        
        Args:
            messages: Messages to estimate
            
        Returns:
            Estimated token count
        """
        ...
