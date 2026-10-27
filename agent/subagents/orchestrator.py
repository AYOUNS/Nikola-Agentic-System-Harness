"""Sub-agent orchestration - delegation and execution.

Implements the delegate() tool that spawns sub-agent instances.
Sub-agents with 'ask' policies are auto-denied.

Week 3: Integrated with observability hooks to record sub-agent execution
in parent's observability data.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from agent.engine import TurnLimits, run_turn
from agent.hooks.instrumentation import InstrumentedToolAdapter, subagent_scope
from agent.permissions import PermissionModule
from agent.subagent_adapters import (
    SubAgentContextAdapter,
    SubAgentLLMAdapter,
    SubAgentToolAdapter,
)
from agent.subagents.progress import SubAgentProgress
from agent.subagents.roles import SubAgentRole, get_role
from agent.tools import make_scoped_registry


def _load_orchestrator_template() -> str:
    """Load the sub-agent orchestrator prompt template.
    
    Template lives in agent/prompts/internal/ alongside compaction.md,
    separate from user-facing mode prompts. Contains placeholders for
    runtime values: {role_name}, {role_description}, {task}, etc.
    
    Returns:
        Template string with placeholders
        
    Raises:
        FileNotFoundError: If template file doesn't exist
    """
    template_file = Path(__file__).resolve().parent.parent / "prompts" / "internal" / "subagent_orchestrator.md"
    if not template_file.exists():
        raise FileNotFoundError(f"Sub-agent orchestrator template not found: {template_file}")
    return template_file.read_text(encoding="utf-8").strip()


class SubAgentContext:
    """Context for a running sub-agent instance.
    
    Tracks the sub-agent's identity, role, and execution state.
    """
    
    def __init__(self, role: SubAgentRole, task: str, parent_session_id: str):
        """Initialize a sub-agent context.
        
        Args:
            role: SubAgentRole configuration
            task: Task description from parent
            parent_session_id: Parent agent's session ID
        """
        self.id = str(uuid.uuid4())[:8]  # Short ID for logging
        self.role = role
        self.task = task
        self.parent_session_id = parent_session_id
        self.iteration_count = 0
        self.is_complete = False
        self.result: str | None = None


async def execute_subagent(
    role_name: str,
    task: str,
    parent_session_id: str,
    registry,
    llm_dispatcher,
    workspace: str,
) -> str:
    """Execute a sub-agent with the specified role and task.
    
    This is the core orchestration function that:
    1. Loads the role configuration
    2. Builds a scoped registry containing only the role's allowed tools
    3. Creates a permission gate with auto-deny for 'ask' actions
    4. Runs a simplified ReAct loop with the role's limits
    5. Returns a condensed summary
    6. (Week 3) Emits observability events for parent tracking

    Start, progress and end events bracket the run, so a display can show the
    sub-agent working rather than freezing until it returns — see
    agent.cli.subagent_view. The end event is emitted even when the run raises:
    a card that never closes is worse than one that reports a failure.

    Args:
        role_name: Name of the sub-agent role (e.g. "file-scout")
        task: Task description from parent agent
        parent_session_id: Parent agent's session ID
        registry: Parent ToolRegistry — scoped copy is built from this
        llm_dispatcher: LLM dispatcher function (non-streaming), sync or async
        workspace: Workspace root path

    Returns:
        Condensed result string (max role.max_result_chars)
        
    Raises:
        ValueError: If role_name is invalid
    """
    # Load role configuration
    try:
        role = get_role(role_name)
    except ValueError as err:
        return f"Error: {err}"
    
    # Create context
    context = SubAgentContext(role, task, parent_session_id)

    # Track progress for the live display
    progress = SubAgentProgress(
        parent_session_id=parent_session_id,
        subagent_id=context.id,
        role_name=role_name,
    )

    # Build a scoped registry — only tools on the role's allowlist are visible
    scoped_registry = make_scoped_registry(registry, role.allowlist)

    # Create permission module for sub-agent (no confirmation, ASK policies auto-denied)
    permission_module = PermissionModule(is_subagent=True)

    # Build system prompt from template
    template = _load_orchestrator_template()
    system_prompt = template.format(
        role_name=role.name,
        role_description=role.description,
        task=task,
        role_instructions=role.system_prompt_suffix,
        max_iterations=role.max_iterations,
        max_result_chars=role.max_result_chars,
    )

    # Initialize messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    # Build adapters for ReActEngine. Both report what they see to `progress`,
    # which is what keeps a live display current while the loop runs.
    llm_adapter = SubAgentLLMAdapter(
        dispatch=llm_dispatcher,
        on_response=progress.record_response,
    )
    tool_adapter = InstrumentedToolAdapter(
        SubAgentToolAdapter(
            registry=scoped_registry,
            permission_module=permission_module,
            on_tool_calls=progress.record_tool_calls,
        )
    )
    context_adapter = SubAgentContextAdapter()

    # Configure limits
    limits = TurnLimits(max_iterations=role.max_iterations)

    with subagent_scope(parent_session_id, context.id, role_name, task) as sub:
        sub.tools_used = progress.tools_used  # live list; read on both paths

        result = await run_turn(
            messages=messages,
            llm_adapter=llm_adapter,
            tool_adapter=tool_adapter,
            context_adapter=context_adapter,
            limits=limits,
        )

        # Extract final answer and truncate if needed
        final_answer = result.answer
        if len(final_answer) > role.max_result_chars:
            final_answer = final_answer[:role.max_result_chars] + "...[truncated]"

        sub.record(result)   # fixes §1.6 — [Error: ...] is no longer a success
        sub.result = final_answer
        context.iteration_count = result.iterations
        context.is_complete = sub.success
        context.result = final_answer

    return final_answer


def make_delegate_tool(
    parent_session_id: str,
    registry,
    llm_dispatcher,
    workspace: str,
) -> dict[str, Any]:
    """Create the delegate() tool for spawning sub-agents.
    
    Args:
        parent_session_id: Parent agent's session ID
        registry: ToolRegistry instance
        llm_dispatcher: LLM dispatcher function
        workspace: Workspace root path
        
    Returns:
        Tool dict with name, fn, and schema
    """
    from agent.subagents.roles import list_roles
    
    available_roles = list_roles()
    
    async def delegate_fn(role: str, task: str) -> str:
        """Delegate a task to a specialized sub-agent.
        
        Args:
            role: Sub-agent role name (e.g. "file-scout", "scheduler")
            task: Task description - be specific about what you need
            
        Returns:
            Condensed result from sub-agent
        """
        return await execute_subagent(
            role_name=role,
            task=task,
            parent_session_id=parent_session_id,
            registry=registry,
            llm_dispatcher=llm_dispatcher,
            workspace=workspace,
        )
    
    schema = {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": (
                f"Delegate a task to a specialized sub-agent. "
                f"Available roles: {', '.join(available_roles)}. "
                f"Each sub-agent has limited capabilities and returns a condensed summary. "
                f"Use for: tasks that require many tool calls but only need a short result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": available_roles,
                        "description": f"Sub-agent role. Options: {', '.join(available_roles)}",
                    },
                    "task": {
                        "type": "string",
                        "description": "Task description. Be specific about what you need the sub-agent to do and what information to return.",
                    },
                },
                "required": ["role", "task"],
            },
        },
    }
    
    return {
        "name": "delegate",
        "fn": delegate_fn,
        "schema": schema,
    }



async def execute_subagents_parallel(
    tasks: list[dict[str, str]],
    parent_session_id: str,
    registry,
    llm_dispatcher,
    workspace: str,
) -> list[dict[str, Any]]:
    """Execute multiple sub-agents in parallel using asyncio.gather.
    
    Each sub-agent runs in its own isolated context with its role's permissions.
    All sub-agents execute concurrently and their results are collected together.
    
    Args:
        tasks: List of task definitions, each with "role" and "task" keys
               Example: [{"role": "file-scout", "task": "Find all TODOs"}]
        parent_session_id: Parent agent's session ID (for observability correlation)
        registry: Parent ToolRegistry (each sub-agent gets a scoped copy)
        llm_dispatcher: LLM dispatcher function (non-streaming, OpenAI SDK compatible)
        workspace: Workspace root path
        
    Returns:
        List of result dicts with structure:
        [
            {
                "role": "file-scout",
                "task": "Find all TODOs",
                "result": "Found 15 TODO comments...",
                "success": True,
                "duration_ms": 1234,
                "subagent_id": "abc123"
            },
            ...
        ]
        
    Example:
        results = await execute_subagents_parallel(
            tasks=[
                {"role": "file-scout", "task": "Search agent/ for imports"},
                {"role": "file-scout", "task": "Search tests/ for fixtures"},
            ],
            parent_session_id="parent-xyz",
            registry=tool_registry,
            llm_dispatcher=dispatcher,
            workspace="/workspace",
        )
        
    See also: execute_subagent (sequential execution)
    """
    
    async def execute_one_task(task_def: dict[str, str]) -> dict[str, Any]:
        """Execute a single sub-agent task and capture metadata.
        
        Wraps execute_subagent with timing and error handling for parallel execution.
        Each sub-agent emits its own observability events via execute_subagent.
        """
        start_time = time.perf_counter()
        subagent_id = str(uuid.uuid4())[:8]  # Pre-generate ID for result tracking
        
        try:
            result = await execute_subagent(
                role_name=task_def["role"],
                task=task_def["task"],
                parent_session_id=parent_session_id,
                registry=registry,
                llm_dispatcher=llm_dispatcher,
                workspace=workspace,
            )
            success = not result.startswith("Error:")
        except Exception as err:
            result = f"Error: Exception in parallel sub-agent: {err}"
            success = False
        
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        
        return {
            "role": task_def["role"],
            "task": task_def["task"],
            "result": result,
            "success": success,
            "duration_ms": duration_ms,
            "subagent_id": subagent_id,
        }
    
    # Execute all sub-agents concurrently using asyncio.gather
    # asyncio.gather preserves order of inputs in outputs
    results = await asyncio.gather(*[execute_one_task(task) for task in tasks])
    
    return list(results)


def make_delegate_parallel_tool(
    parent_session_id: str,
    registry,
    llm_dispatcher,
    workspace: str,
) -> dict[str, Any]:
    """Create the delegate_parallel() tool for spawning multiple sub-agents concurrently.
    
    This tool allows the parent agent to execute independent tasks in parallel,
    reducing total execution time and keeping parent context compact.
    
    Args:
        parent_session_id: Parent agent's session ID
        registry: ToolRegistry instance (parent's full registry)
        llm_dispatcher: LLM dispatcher function (non-streaming)
        workspace: Workspace root path
        
    Returns:
        Tool dict with name, fn, and schema (OpenAI SDK ChatCompletionToolParam format)
        
    Example registration:
        tool = make_delegate_parallel_tool(
            parent_session_id=session.id,
            registry=tool_registry,
            llm_dispatcher=dispatcher,
            workspace="/workspace",
        )
        registry.register(**tool)
        
    See also: make_delegate_tool (sequential delegation)
    """
    from agent.subagents.roles import list_roles
    
    available_roles = list_roles()
    
    async def delegate_parallel_fn(tasks: list[dict[str, str]]) -> str:
        """Execute multiple sub-agent tasks in parallel.
        
        All tasks run concurrently and return together as a JSON array.
        Each sub-agent has its own isolated context and permission policy.
        
        Args:
            tasks: Array of task objects, each with "role" and "task" fields.
                   Example:
                   [
                       {"role": "file-scout", "task": "Find all TODOs in agent/"},
                       {"role": "file-scout", "task": "Find all FIXMEs in tests/"},
                       {"role": "file-scout", "task": "List all config files"}
                   ]
        
        Returns:
            JSON string containing array of results with metadata:
            [
                {
                    "role": "file-scout",
                    "task": "Find all TODOs in agent/",
                    "result": "Found 15 TODO comments...",
                    "success": true,
                    "duration_ms": 1234
                },
                ...
            ]
            
        Note: Maximum 5 parallel tasks to avoid overwhelming the system.
              Use sequential delegate() if you need more than 5 dependent steps.
        """
        # Validate input structure
        if not tasks:
            return json.dumps({"error": "tasks array cannot be empty"})
        
        if not isinstance(tasks, list):
            return json.dumps({"error": "tasks must be an array"})
        
        # Validate each task object
        for i, task_def in enumerate(tasks):
            if not isinstance(task_def, dict):
                return json.dumps({
                    "error": f"Task at index {i} must be an object with 'role' and 'task' fields"
                })
            
            if "role" not in task_def:
                return json.dumps({
                    "error": f"Task at index {i} missing required field 'role'"
                })
            
            if "task" not in task_def:
                return json.dumps({
                    "error": f"Task at index {i} missing required field 'task'"
                })
            
            if task_def["role"] not in available_roles:
                return json.dumps({
                    "error": f"Task at index {i} has invalid role '{task_def['role']}'. Available: {', '.join(available_roles)}"
                })
        
        # Execute all tasks in parallel
        results = await execute_subagents_parallel(
            tasks=tasks,
            parent_session_id=parent_session_id,
            registry=registry,
            llm_dispatcher=llm_dispatcher,
            workspace=workspace,
        )
        
        # Return results as formatted JSON for LLM consumption
        return json.dumps(results, indent=2)
    
    # Build OpenAI-compatible tool schema
    schema = {
        "type": "function",
        "function": {
            "name": "delegate_parallel",
            "description": (
                f"Execute multiple independent sub-agent tasks in parallel. "
                f"Available roles: {', '.join(available_roles)}. "
                f"All tasks run concurrently and return together. "
                f"Use when you have independent tasks that don't depend on each other. "
                f"Example: search multiple directories, analyze multiple files, "
                f"or perform multiple independent checks simultaneously. "
                f"Maximum 5 parallel tasks. Total time = slowest task (not sum of all)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": (
                            "Array of task definitions. Each task runs in parallel. "
                            "Each task must have 'role' and 'task' fields."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "enum": available_roles,
                                    "description": f"Sub-agent role name. Options: {', '.join(available_roles)}",
                                },
                                "task": {
                                    "type": "string",
                                    "description": (
                                        "Task description for this sub-agent. "
                                        "Be specific about what information to gather or action to take."
                                    ),
                                },
                            },
                            "required": ["role", "task"],
                        },
                        "minItems": 1,
                        "maxItems": 5,  # Limit parallelism to avoid resource exhaustion
                    },
                },
                "required": ["tasks"],
            },
        },
    }
    
    return {
        "name": "delegate_parallel",
        "fn": delegate_parallel_fn,
        "schema": schema,
    }
