"""Layer 3 — effect factories and dispatcher.

Never touches os.environ.
Receives only the OpenAI client object and config dict.

Config is the single source of truth — model and timeout are resolved
once at factory construction time, not per-call.

Communicates with the OpenAI API using the SDK's own typed request objects:
  CompletionCreateParamsNonStreaming — for make_chat_completions_effect()
  CompletionCreateParamsStreaming    — for make_chat_completions_stream_effect()

LLM call instrumentation (timing, LLMCallEvent) is applied by the caller via
instrument_dispatch / instrument_stream_dispatch from agent.hooks.instrumentation,
not here. This module is pure transport.

The one thing it reads off the ambient run is the X-Mode header — which side of
the delegation boundary a request comes from. That belongs here because it is a
property of the request itself, and because this is the single chokepoint every
completion passes through, streaming or not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from openai import OpenAI
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
)
from openai.types.chat.completion_create_params import (
    CompletionCreateParamsNonStreaming,
    CompletionCreateParamsStreaming,
)

from agent.hooks.context import current_context

MODE_HEADER = "X-Mode"
"""Names the caller on every outbound completion: "agent" or "subagent"."""


def mode_headers() -> dict[str, str]:
    """The X-Mode header for whichever run is making this call.

    Read per call rather than resolved at construction: a sub-agent is handed
    its parent's dispatch callable, so one closure serves both sides of the
    boundary and only the ambient RunContext can tell them apart. The context
    is a task-local ContextVar, so parallel sub-agents each report their own
    mode instead of racing over a shared value.

    Outside any run — compaction, a warm-up call — there is no parent, so the
    request reports "agent".
    """
    return {MODE_HEADER: current_context().mode}


def make_chat_completions_effect(
    client: OpenAI,
    config: dict[str, Any],
) -> Callable[[CompletionCreateParamsNonStreaming], ChatCompletion]:
    """make_chat_completions_effect returns a non-streaming dispatch callable.

    Resolves model and timeout from config once at construction time.
    The returned callable accepts a CompletionCreateParamsNonStreaming request
    and posts it to the InnKube API, returning the full response.
    Callers only need to provide messages in the request — all other params
    are set from config.yaml.

    client: configured openai.OpenAI instance from make_client().
    config: parsed config dict — single source of truth for model and timeout.

    Returns a callable: dispatch(request) -> ChatCompletion.

    Example:
        dispatch = make_chat_completions_effect(client, cfg)
        result   = dispatch({"messages": [ChatCompletionUserMessageParam(role="user", content="Hi")]})

    See also: make_chat_completions_stream_effect, make_client.
    """
    # Resolved once — config is the single source of truth.
    model: str = config["llm"]["model"]
    timeout: int = config["llm"]["timeout_seconds"]
    # Default completion cap from loop.max_tokens. A caller that sets its own (compaction,
    # with its much smaller summary_max_tokens) keeps it — this only fills in when omitted.
    max_tokens: int | None = config.get("loop", {}).get("max_tokens")

    def dispatch(request: CompletionCreateParamsNonStreaming) -> ChatCompletion:
        """dispatch posts a non-streaming completion request to the API.

        Caller provides messages; model and timeout come from config.

        request: typed CompletionCreateParamsNonStreaming object. Every key the
                 caller sets is forwarded (messages, tools, tool_choice, ...);
                 model, timeout and stream are overridden from config, and
                 max_tokens is defaulted from config when the caller omits it.

        Returns the full ChatCompletion response from the API.
        """
        params: CompletionCreateParamsNonStreaming = {
            **({"max_tokens": max_tokens} if max_tokens is not None else {}),
            **request,
            "model": model,
            "timeout": timeout,
            "stream": False,
        }
        return client.chat.completions.create(  # type: ignore[call-overload]
            **params, extra_headers=mode_headers()
        )

    return dispatch


def make_chat_completions_stream_effect(
    client: OpenAI,
    config: dict[str, Any],
) -> Callable[[CompletionCreateParamsStreaming], Iterator[ChatCompletionChunk]]:
    """make_chat_completions_stream_effect returns a streaming dispatch callable.

    Resolves model and timeout from config once at construction time.
    The returned callable accepts a CompletionCreateParamsStreaming request
    and opens a streaming connection to the InnKube API, yielding chunks
    as they arrive. Callers only need to provide messages in the request.

    client: configured openai.OpenAI instance from make_client().
    config: parsed config dict — single source of truth for model and timeout.

    Returns a callable: dispatch(request) -> Iterator[ChatCompletionChunk].

    Example:
        dispatch = make_chat_completions_stream_effect(client, cfg)
        for chunk in dispatch({"messages": [ChatCompletionUserMessageParam(role="user", content="Hi")]}):
            print(chunk.choices[0].delta.content, end="", flush=True)

    See also: make_chat_completions_effect, make_client.
    """
    # Resolved once — config is the single source of truth.
    model: str = config["llm"]["model"]
    timeout: int = config["llm"]["timeout_seconds"]
    # Default completion cap from loop.max_tokens. A caller that sets its own (compaction,
    # with its much smaller summary_max_tokens) keeps it — this only fills in when omitted.
    max_tokens: int | None = config.get("loop", {}).get("max_tokens")

    def dispatch(request: CompletionCreateParamsStreaming) -> Iterator[ChatCompletionChunk]:
        """dispatch opens a streaming completion request to the API.

        Caller provides messages; model and timeout come from config.

        request: typed CompletionCreateParamsStreaming object. Every key the
                 caller sets is forwarded (messages, tools, tool_choice, ...);
                 model, timeout and stream are overridden from config, and
                 max_tokens is defaulted from config when the caller omits it.

        Yields ChatCompletionChunk objects as they arrive from the API.
        """
        params: CompletionCreateParamsStreaming = {
            **({"max_tokens": max_tokens} if max_tokens is not None else {}),
            # Ask for a final usage chunk so streamed calls report token counts too
            "stream_options": {"include_usage": True},
            **request,
            "model": model,
            "timeout": timeout,
            "stream": True,
        }
        yield from client.chat.completions.create(  # type: ignore[call-overload]
            **params, extra_headers=mode_headers()
        )

    return dispatch
