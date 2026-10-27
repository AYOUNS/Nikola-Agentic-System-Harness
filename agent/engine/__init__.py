"""ReAct loop engine.

Pure state machine for ReAct (Reasoning + Acting) loops with injected adapters.

Public API:
    run_turn()       - Execute a single ReAct turn
    TurnResult       - Result dataclass with answer and metadata
    TurnLimits       - Configuration dataclass for guards
    LLMAdapter       - Protocol for LLM dispatch
    ToolAdapter      - Protocol for tool dispatch
    ContextAdapter   - Protocol for context management

Usage:
    from agent.engine import run_turn, TurnLimits
    
    result = await run_turn(
        messages=conversation_history,
        llm_adapter=my_llm_adapter,
        tool_adapter=my_tool_adapter,
        context_adapter=my_context_adapter,
        limits=TurnLimits(max_iterations=10),
    )
    
    print(result.answer)
    print(f"Took {result.iterations} iterations")
"""

from agent.engine.adapters import ContextAdapter, LLMAdapter, ToolAdapter
from agent.engine.core import run_turn
from agent.engine.types import ToolOutcome, TurnLimits, TurnResult

__all__ = [
    "ContextAdapter",
    "LLMAdapter",
    "ToolAdapter",
    "ToolOutcome",
    "TurnLimits",
    "TurnResult",
    "run_turn",
]
