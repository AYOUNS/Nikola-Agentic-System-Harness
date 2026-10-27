"""ReAct loop engine core implementation.

Pure state machine that implements the ReAct (Reasoning + Acting) loop.
All external dependencies are injected via adapters, making this testable
without mocking LLMs, tools, or compaction.

The engine knows nothing about:
- Streaming vs non-streaming
- Permission checks
- Registry implementation
- Compaction strategy
- Observability hooks

It just knows: call LLM → parse tool calls → dispatch → repeat until done.
"""

from __future__ import annotations

import time

from openai.types.chat import ChatCompletionMessageParam

from agent.engine.adapters import ContextAdapter, LLMAdapter, ToolAdapter
from agent.engine.types import TurnLimits, TurnResult


async def run_turn(
    messages: list[ChatCompletionMessageParam],
    llm_adapter: LLMAdapter,
    tool_adapter: ToolAdapter,
    context_adapter: ContextAdapter,
    limits: TurnLimits,
) -> TurnResult:
    """Execute a single ReAct turn.
    
    A turn consists of multiple iterations of:
    1. (Optional) Compact context if approaching token budget
    2. Call LLM with current messages
    3. If LLM returns final answer (no tool calls) → done
    4. If LLM returns tool calls → execute them, append observations, loop
    
    The loop terminates when:
    - LLM returns a final answer (finish_reason == "stop")
    - Iteration limit reached
    - Token budget exceeded (if limits.max_tokens is set)
    - Time budget exceeded (if limits.max_time_seconds is set)
    
    Args:
        messages: Initial conversation history (will be mutated)
        llm_adapter: Adapter for LLM dispatch
        tool_adapter: Adapter for tool dispatch
        context_adapter: Adapter for context compaction
        limits: Turn limits (iterations, tokens, time)
        
    Returns:
        TurnResult with final answer and execution metadata
    """
    start_time = time.perf_counter()
    tool_calls_made = 0
    
    for iteration in range(limits.max_iterations):
        # Check time budget
        if limits.max_time_seconds is not None:
            elapsed = time.perf_counter() - start_time
            if elapsed > limits.max_time_seconds:
                return TurnResult(
                    answer=f"[Loop terminated: time budget ({limits.max_time_seconds}s) exceeded]",
                    iterations=iteration,
                    guard_fired="max_time",
                    tool_calls_made=tool_calls_made,
                )
        
        # Compact context if needed
        if limits.max_tokens is not None:
            current_tokens = context_adapter.estimate_tokens(messages)
            if current_tokens > limits.max_tokens:
                messages[:] = context_adapter.compact(messages, limits.max_tokens)
        
        # Get tool schemas from adapter
        tools = tool_adapter.get_schemas()
        
        # Call LLM
        try:
            response = await llm_adapter.call(messages, tools)
        except Exception as err:
            # LLM failure - return error
            return TurnResult(
                answer=f"[Error: LLM call failed: {err}]",
                iterations=iteration + 1,
                guard_fired=None,
                tool_calls_made=tool_calls_made,
            )
        
        # Extract assistant message
        if not response.choices:
            return TurnResult(
                answer="[Error: No response from LLM]",
                iterations=iteration + 1,
                guard_fired=None,
                tool_calls_made=tool_calls_made,
            )
        
        choice = response.choices[0]
        message = choice.message
        
        # Add assistant message to history
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [tc.model_dump() for tc in (message.tool_calls or [])],
        })
        
        # Check if done (no tool calls = final answer)
        if not message.tool_calls:
            return TurnResult(
                answer=message.content or "",
                iterations=iteration + 1,
                guard_fired=None,
                tool_calls_made=tool_calls_made,
            )
        
        # Execute tool calls
        tool_calls_made += len(message.tool_calls)
        tool_outcomes = await tool_adapter.dispatch_all(message.tool_calls)
        messages.extend(outcome.message for outcome in tool_outcomes)
    
    # Hit iteration limit
    return TurnResult(
        answer=f"[Loop terminated: max iterations ({limits.max_iterations}) reached]",
        iterations=limits.max_iterations,
        guard_fired="max_iterations",
        tool_calls_made=tool_calls_made,
    )
