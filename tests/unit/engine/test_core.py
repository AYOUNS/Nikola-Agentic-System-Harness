"""Basic smoke tests for the ReActEngine.

Tests the pure state machine with fake adapters (no real LLM/tools needed).
"""

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice, CompletionUsage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from agent.engine import ToolOutcome, TurnLimits, run_turn


class FakeLLMAdapter:
    """Fake LLM that returns predefined responses."""
    
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
    
    async def call(self, messages, tools):
        if self.call_count >= len(self.responses):
            raise ValueError("No more responses available")
        
        response = self.responses[self.call_count]
        self.call_count += 1
        
        # Build ChatCompletion
        return ChatCompletion(
            id="fake",
            model="fake",
            object="chat.completion",
            created=0,
            choices=[Choice(
                index=0,
                message=ChatCompletionMessage(
                    role="assistant",
                    content=response.get("content"),
                    tool_calls=response.get("tool_calls"),
                ),
                finish_reason=response.get("finish_reason", "stop"),
            )],
            usage=CompletionUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )


class FakeToolAdapter:
    """Fake tool adapter that tracks calls."""
    
    def __init__(self):
        self.dispatched = []
    
    def get_schemas(self):
        return [{"type": "function", "function": {"name": "test_tool"}}]
    
    async def dispatch_all(self, tool_calls):
        self.dispatched.extend(tool_calls)

        # Return ToolOutcome objects (one per call)
        return [
            ToolOutcome(
                message={"role": "tool", "tool_call_id": tc.id, "content": f"Result of {tc.function.name}"},
                tool_name=tc.function.name,
                arguments={},
                permission_outcome="none",
                duration_ms=0.0,
                success=True,
            )
            for tc in tool_calls
        ]


class FakeContextAdapter:
    """Fake context adapter (no compaction)."""
    
    def compact(self, messages, budget):
        return messages
    
    def estimate_tokens(self, messages):
        return len(messages) * 100  # Rough estimate


@pytest.mark.asyncio
async def test_engine_simple_answer():
    """Test engine with LLM that returns immediate answer."""
    llm = FakeLLMAdapter([
        {"content": "Hello, user!", "finish_reason": "stop"}
    ])
    tool = FakeToolAdapter()
    context = FakeContextAdapter()
    
    result = await run_turn(
        messages=[{"role": "user", "content": "Hi"}],
        llm_adapter=llm,
        tool_adapter=tool,
        context_adapter=context,
        limits=TurnLimits(max_iterations=10),
    )
    
    assert result.answer == "Hello, user!"
    assert result.iterations == 1
    assert result.guard_fired is None
    assert result.tool_calls_made == 0
    assert len(tool.dispatched) == 0


@pytest.mark.asyncio
async def test_engine_with_tool_call():
    """Test engine with LLM that makes a tool call then answers."""
    llm = FakeLLMAdapter([
        # First response: tool call
        {
            "content": None,
            "tool_calls": [
                ChatCompletionMessageToolCall(
                    id="call_123",
                    type="function",
                    function=Function(name="test_tool", arguments='{"arg":"value"}'),
                )
            ],
            "finish_reason": "tool_calls",
        },
        # Second response: final answer
        {"content": "Done!", "finish_reason": "stop"},
    ])
    tool = FakeToolAdapter()
    context = FakeContextAdapter()
    
    result = await run_turn(
        messages=[{"role": "user", "content": "Do something"}],
        llm_adapter=llm,
        tool_adapter=tool,
        context_adapter=context,
        limits=TurnLimits(max_iterations=10),
    )
    
    assert result.answer == "Done!"
    assert result.iterations == 2
    assert result.guard_fired is None
    assert result.tool_calls_made == 1
    assert len(tool.dispatched) == 1


@pytest.mark.asyncio
async def test_engine_iteration_limit():
    """Test that engine respects iteration limit."""
    # LLM that keeps making tool calls
    llm = FakeLLMAdapter([
        {
            "content": None,
            "tool_calls": [
                ChatCompletionMessageToolCall(
                    id=f"call_{i}",
                    type="function",
                    function=Function(name="test_tool", arguments='{}'),
                )
            ],
            "finish_reason": "tool_calls",
        }
        for i in range(20)  # More than our limit
    ])
    tool = FakeToolAdapter()
    context = FakeContextAdapter()
    
    result = await run_turn(
        messages=[{"role": "user", "content": "Loop forever"}],
        llm_adapter=llm,
        tool_adapter=tool,
        context_adapter=context,
        limits=TurnLimits(max_iterations=5),
    )
    
    assert "max iterations" in result.answer.lower()
    assert result.iterations == 5
    assert result.guard_fired == "max_iterations"
    assert result.tool_calls_made == 5


@pytest.mark.asyncio
async def test_engine_multiple_tool_calls():
    """Test engine with multiple tool calls in one response."""
    llm = FakeLLMAdapter([
        {
            "content": None,
            "tool_calls": [
                ChatCompletionMessageToolCall(
                    id="call_1",
                    type="function",
                    function=Function(name="tool_a", arguments='{}'),
                ),
                ChatCompletionMessageToolCall(
                    id="call_2",
                    type="function",
                    function=Function(name="tool_b", arguments='{}'),
                ),
            ],
            "finish_reason": "tool_calls",
        },
        {"content": "All done!", "finish_reason": "stop"},
    ])
    tool = FakeToolAdapter()
    context = FakeContextAdapter()
    
    result = await run_turn(
        messages=[{"role": "user", "content": "Do two things"}],
        llm_adapter=llm,
        tool_adapter=tool,
        context_adapter=context,
        limits=TurnLimits(max_iterations=10),
    )
    
    assert result.answer == "All done!"
    assert result.iterations == 2
    assert result.tool_calls_made == 2
    assert len(tool.dispatched) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
