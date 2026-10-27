"""Where lifecycle events are emitted from.

Instrumentation lives here rather than inside the code it measures. Two shapes
cover everything the agent does:

- **Scopes** bracket a run (``run_scope``, ``subagent_scope``): they emit the
  start event, yield a mutable outcome for the caller to fill in, and emit the
  end event from a ``finally`` so a run that raises still closes its gauge.
- **Wrappers** sit on the ports an agent talks through — the LLM dispatch
  callable (``instrument_dispatch``) and the engine's ToolAdapter
  (``InstrumentedToolAdapter``). Composition applies them once, in
  agent.loop, agent.subagents.orchestrator and agent.cli.

The point of the wrappers is that instrumentation stops being something each
call site remembers to write. Every tool call reaches the engine through a
ToolAdapter, so one wrapper covers the main loop and sub-agents at once, and
refactoring the dispatch code underneath cannot quietly drop a metric — which
is exactly how the tool and permission metrics were lost once already.

See also: agent.hooks.context for the ids these attach to events, and
agent.memory.instrumentation for the same idea applied to the memory store.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageToolCall,
)

from agent.engine.adapters import ToolAdapter
from agent.engine.types import ToolOutcome, TurnResult
from agent.hooks.context import RunContext, current_context, use_context
from agent.hooks.types import (
    LLMCallEvent,
    RunEndEvent,
    RunStartEvent,
    SubAgentEndEvent,
    SubAgentStartEvent,
    ToolCallEvent,
    get_hook_registry,
)

ERROR_PREFIX = "[Error:"
"""How the engine reports a failure it caught: as an answer, not an exception."""


def classify_status(answer: str, guard_fired: str | None) -> str:
    """Map a finished turn onto a run status: "success", "guard" or "error".

    The engine returns LLM failures as an answer rather than raising, so
    "did it raise" is the wrong question — the answer prefix is. Top-level runs
    and sub-agents share this because they used to classify differently, and
    sub-agent failures were being counted as successes.
    """
    if guard_fired:
        return "guard"
    return "error" if answer.startswith(ERROR_PREFIX) else "success"


@dataclass
class RunOutcome:
    """What a run scope reports when it closes. The caller fills it in.

    Defaults describe a run that never got to report — an exception on the way
    out is an error that ran zero iterations.
    """

    status: str = "error"
    iterations: int = 0
    tool_calls: int = 0

    def record(self, result: TurnResult) -> None:
        """Take status, iteration count and tool count from a finished turn."""
        self.status = classify_status(result.answer, result.guard_fired)
        self.iterations = result.iterations
        self.tool_calls = result.tool_calls_made


@dataclass
class SubAgentOutcome:
    """What a sub-agent scope reports when it closes. The caller fills it in."""

    success: bool = False
    result: str = ""
    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)

    def record(self, result: TurnResult) -> None:
        """Take success and iteration count from a finished turn."""
        self.success = classify_status(result.answer, result.guard_fired) == "success"
        self.iterations = result.iterations


@contextmanager
def run_scope(session_id: str, agent: str = "main") -> Iterator[RunOutcome]:
    """Bracket one top-level run with its start and end events.

    Installs a RunContext for the body, so everything emitted underneath —
    LLM calls, tool calls, memory operations — reports the same session.

    Args:
        session_id: Session this run belongs to.
        agent: Agent label for the events.

    Yields:
        A RunOutcome to fill in, usually via ``outcome.record(result)``.

    Example:
        with run_scope(session_id) as run:
            result = await run_turn(...)
            run.record(result)
    """
    hooks = get_hook_registry()
    run_id = str(uuid.uuid4())
    outcome = RunOutcome()
    start = time.perf_counter()

    with use_context(RunContext(session_id=session_id, agent=agent, run_id=run_id)):
        hooks.emit_run_start(RunStartEvent(session_id=session_id, run_id=run_id, agent=agent))
        try:
            yield outcome
        finally:
            hooks.emit_run_end(
                RunEndEvent(
                    session_id=session_id,
                    run_id=run_id,
                    agent=agent,
                    status=outcome.status,
                    iterations=outcome.iterations,
                    tool_calls=outcome.tool_calls,
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            )


@contextmanager
def subagent_scope(
    parent_session_id: str,
    subagent_id: str,
    role_name: str,
    task: str,
) -> Iterator[SubAgentOutcome]:
    """Bracket one sub-agent run with its start and end events.

    The context installed for the body names the sub-agent, not its parent, so
    a tool call made inside a sub-agent is attributable to it.

    An exception closes the run out before it leaves: subscribers that opened
    something on the start event (a card, a span) have no other signal.

    Yields:
        A SubAgentOutcome to fill in. Point ``tools_used`` at the live list the
        run accumulates and the end event will report it on both paths.
    """
    hooks = get_hook_registry()
    outcome = SubAgentOutcome()
    start = time.perf_counter()
    ctx = RunContext(
        session_id=subagent_id,
        agent=role_name,
        parent_session_id=parent_session_id,
    )

    with use_context(ctx):
        hooks.emit_subagent_start(
            SubAgentStartEvent(
                parent_session_id=parent_session_id,
                subagent_id=subagent_id,
                role_name=role_name,
                task=task,
            )
        )
        try:
            yield outcome
        except BaseException as err:
            outcome.success = False
            outcome.result = f"Error: {type(err).__name__}: {err}"
            raise
        finally:
            hooks.emit_subagent_end(
                SubAgentEndEvent(
                    parent_session_id=parent_session_id,
                    subagent_id=subagent_id,
                    role_name=role_name,
                    success=outcome.success,
                    result=outcome.result,
                    iterations=outcome.iterations,
                    tools_used=list(outcome.tools_used),
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            )


class InstrumentedToolAdapter:
    """A ToolAdapter that reports what the adapter it wraps did.

    Satisfies the ToolAdapter protocol, so the engine cannot tell the
    difference and neither can the adapter underneath. Events are emitted per
    tool call, after the batch returns — durations are measured per call by the
    dispatch code, so only the event timestamps are batched.

    Example:
        tool_adapter = InstrumentedToolAdapter(MainLoopToolAdapter(registry))
    """

    def __init__(self, inner: ToolAdapter) -> None:
        """Wrap ``inner``, leaving its behaviour unchanged."""
        self.inner = inner

    def get_schemas(self) -> list[dict]:
        """Tool schemas from the wrapped adapter."""
        return self.inner.get_schemas()

    async def dispatch_all(
        self,
        tool_calls: list[ChatCompletionMessageToolCall],
    ) -> list[ToolOutcome]:
        """Dispatch through the wrapped adapter, emitting one event per call."""
        outcomes = await self.inner.dispatch_all(tool_calls)
        session_id = current_context().session_id
        hooks = get_hook_registry()
        for outcome in outcomes:
            hooks.emit_tool_call(
                ToolCallEvent(
                    session_id=session_id,
                    tool_name=outcome.tool_name,
                    arguments=outcome.arguments,
                    result=str(outcome.message["content"]),
                    permission_outcome=outcome.permission_outcome,
                    duration_ms=outcome.duration_ms,
                    success=outcome.success,
                )
            )
        return outcomes


def _emit_llm_call(model: str, usage: Any, duration_ms: float, success: bool) -> None:
    """Emit one LLMCallEvent, tolerating a response that carried no usage block."""
    get_hook_registry().emit_llm_call(
        LLMCallEvent(
            session_id=current_context().session_id,
            model=model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            duration_ms=duration_ms,
            success=success,
        )
    )


def instrument_dispatch(
    dispatch: Callable[[Any], ChatCompletion],
    model: str,
) -> Callable[[Any], ChatCompletion]:
    """Wrap a non-streaming dispatch callable so every call reports an LLMCallEvent.

    Wrapping the callable rather than the LLMAdapter above it is deliberate:
    the same callable is what compaction's summarizer and the sub-agent
    orchestrator are handed, so all three are counted without knowing it.

    Args:
        dispatch: Callable built by make_chat_completions_effect.
        model: Configured model, reported when a call fails before a response
            names the model that actually served it.
    """

    def instrumented(request: Any) -> ChatCompletion:
        start = time.perf_counter()
        try:
            response = dispatch(request)
        except Exception:
            _emit_llm_call(model, None, (time.perf_counter() - start) * 1000, success=False)
            raise
        _emit_llm_call(
            getattr(response, "model", None) or model,
            getattr(response, "usage", None),
            (time.perf_counter() - start) * 1000,
            success=True,
        )
        return response

    return instrumented


def instrument_stream_dispatch(
    stream_dispatch: Callable[[Any], Iterator[ChatCompletionChunk]],
    model: str,
) -> Callable[[Any], Iterator[ChatCompletionChunk]]:
    """Wrap a streaming dispatch callable so every stream reports an LLMCallEvent.

    Token counts arrive in a final usage-only chunk (the dispatcher asks for one
    with ``stream_options``) and the served model name rides on the chunks, so
    both are read off the stream as it passes through. A stream the caller
    abandons part-way reports a failure, which is what an abandoned call is.
    """

    def instrumented(request: Any) -> Iterator[ChatCompletionChunk]:
        start = time.perf_counter()
        usage = None
        served_model = model
        success = False
        try:
            for chunk in stream_dispatch(request):
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                chunk_model = getattr(chunk, "model", None)
                if isinstance(chunk_model, str) and chunk_model:
                    served_model = chunk_model
                yield chunk
            success = True
        finally:
            _emit_llm_call(served_model, usage, (time.perf_counter() - start) * 1000, success)

    return instrumented
