"""Layer 2 — type aliases and Protocols for the LLM dispatcher.

Never imports openai directly — only references its types.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from openai.types.chat import ChatCompletion, ChatCompletionChunk


class ChatCompletionCall(Protocol):
    """ChatCompletionCall is the Protocol for a non-streaming completion call.

    Matches the callable signature of openai.OpenAI.chat.completions.create
    when stream=False. Used as the type for the injected service in
    make_chat_completions_effect.

    See also: ChatCompletionStreamCall, CompletionEffect.
    """

    def __call__(self, **kwargs: object) -> ChatCompletion: ...


class ChatCompletionStreamCall(Protocol):
    """ChatCompletionStreamCall is the Protocol for a streaming completion call.

    Matches the callable signature of openai.OpenAI.chat.completions.create
    when stream=True. Used as the type for the injected service in
    make_chat_completions_stream_effect.

    See also: ChatCompletionCall, StreamEffect.
    """

    def __call__(self, **kwargs: object) -> Iterator[ChatCompletionChunk]: ...


# Named type aliases used by dispatcher.py for cleaner return type annotations.
CompletionEffect = ChatCompletionCall
StreamEffect = ChatCompletionStreamCall
