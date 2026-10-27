"""Refactored make_process_turn_fn using ReActEngine.

This replaces the old 190-line make_process_turn_fn with a clean adapter-based
implementation using the ReActEngine.
"""

from collections.abc import Callable, Iterator
from typing import Any

from openai.types.chat import ChatCompletion, ChatCompletionChunk, ChatCompletionMessageParam

from agent.engine import TurnLimits, run_turn
from agent.hooks.instrumentation import InstrumentedToolAdapter, run_scope
from agent.loop_adapters import MainLoopContextAdapter, MainLoopLLMAdapter, MainLoopToolAdapter
from agent.tools import ToolRegistry

OutputKind = str  # "answer" | "reasoning" | "tool"


def make_process_turn_fn_v2(
    dispatch: Callable[[dict], ChatCompletion],
    registry: ToolRegistry,
    config: dict[str, Any],
    stream_dispatch: Callable[[dict], Iterator[ChatCompletionChunk]] | None = None,
    on_output: Callable[[OutputKind, str], None] | None = None,
    permission_module=None,
    confirmation_fn=None,
    session_id: str = "unknown",
    memory_store=None,
    vector_store=None,
) -> Callable[[list[ChatCompletionMessageParam]], str]:
    """Build a process_turn_fn using the ReActEngine.

    Args:
        dispatch: Non-streaming LLM dispatch
        registry: ToolRegistry for tools
        config: Application config
        stream_dispatch: Optional streaming dispatch
        on_output: Optional output sink for streaming
        permission_module: Optional permission module
        confirmation_fn: Optional confirmation callback
        session_id: Session id threaded into every lifecycle event
        memory_store: Optional MemoryStore. Given one, a summary produced by budget-triggered
            compaction is promoted to long-term memory, which is what lets a later session
            recover work this one compacted away.
        vector_store: Optional VectorStore, so promoted summaries are indexed for semantic
            search alongside explicitly remembered facts.

    Returns:
        Async function (messages) -> answer_string
    """
    # Extract limits from config
    max_iterations = config.get("loop", {}).get("max_iterations", 30)

    # TurnLimits.max_tokens is the compaction budget — how many tokens the history may
    # occupy — which is the window minus the space reserved for the reply. It is NOT
    # loop.max_tokens: that is the completion cap handed to the dispatcher. Passing the
    # completion cap here compacted a 256K window down to 8K on every iteration, which
    # dropped the tool results the model had just gathered and left it re-issuing the
    # same calls until it hit max_iterations.
    context_cfg = config.get("context", {})
    window_tokens = context_cfg.get("window_tokens", 8192)
    reserve_output_tokens = context_cfg.get("reserve_output_tokens", 2048)
    # A window that does not exceed its own reserve leaves no budget to compact against.
    # Running uncompacted risks a long context; compacting to zero strips every turn and
    # loops forever, so prefer the former.
    max_tokens = (
        window_tokens - reserve_output_tokens
        if window_tokens > reserve_output_tokens
        else None
    )

    # Build output callbacks for streaming
    renders = stream_dispatch is not None and on_output is not None

    def emit(kind: OutputKind, text: str) -> None:
        if renders and on_output is not None:
            on_output(kind, text)

    # Build adapters
    llm_adapter = MainLoopLLMAdapter(
        dispatch=dispatch,
        stream_dispatch=stream_dispatch,
        on_token=lambda token: emit("answer", token) if renders else None,
        on_reasoning=lambda reasoning: emit("reasoning", reasoning) if renders else None,
    )

    tool_adapter = InstrumentedToolAdapter(
        MainLoopToolAdapter(
            registry=registry,
            permission_module=permission_module,
            confirmation_fn=confirmation_fn,
            on_trace=lambda line: emit("tool", line) if renders else None,
        )
    )

    # The summarizer is the same non-streaming dispatch the loop calls: a recap is an
    # ordinary completion, and a streamed one would render into the user's transcript.
    def promote_summary(summary: str) -> None:
        """Write a tier 3 recap to long-term memory before the dropped turns are gone."""
        from agent.memory import persist_summary_to_memory

        persist_summary_to_memory(summary, session_id, memory_store, vector_store)

    context_adapter = MainLoopContextAdapter(
        config=config,
        dispatch=dispatch,
        on_summary=promote_summary if memory_store is not None else None,
    )

    limits = TurnLimits(
        max_iterations=max_iterations,
        max_tokens=max_tokens,
    )

    # Return the process_turn function
    async def process_turn_fn(messages: list[ChatCompletionMessageParam]) -> str:
        """Execute a single turn using the ReActEngine."""
        with run_scope(session_id, agent="main") as run:
            result = await run_turn(
                messages=messages,
                llm_adapter=llm_adapter,
                tool_adapter=tool_adapter,
                context_adapter=context_adapter,
                limits=limits,
            )
            run.record(result)

        if result.guard_fired:
            emit("error", f"{result.answer}|The task may be too complex. Try breaking it into smaller steps.")

        return result.answer

    return process_turn_fn
