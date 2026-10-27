"""Sub-agent subsystem for delegated task execution.

Implements specialized sub-agents with restricted permissions.
Sub-agents with 'ask' policies are auto-denied.

Architecture:
- roles.py: Role configurations with allowlist/denylist permissions
- orchestrator.py: Delegation and execution logic
- Integration: delegate() and delegate_parallel() tools register in parent's ToolRegistry

Usage (Sequential):
    # Register delegate tool in parent agent
    delegate_tool = make_delegate_tool(
        parent_session_id=session_id,
        registry=registry,
        llm_dispatcher=dispatcher,
        workspace=workspace,
    )
    registry.register(**delegate_tool)
    
    # Parent agent can now call:
    # delegate(role="file-scout", task="Find all references to User class")
    # delegate(role="scheduler", task="Schedule team meeting tomorrow at 10am")

Usage (Parallel):
    # Register parallel delegate tool
    parallel_tool = make_delegate_parallel_tool(
        parent_session_id=session_id,
        registry=registry,
        llm_dispatcher=dispatcher,
        workspace=workspace,
    )
    registry.register(**parallel_tool)
    
    # Parent agent can now execute multiple independent tasks concurrently:
    # delegate_parallel(tasks=[
    #     {"role": "file-scout", "task": "Search agent/ for imports"},
    #     {"role": "file-scout", "task": "Search tests/ for fixtures"},
    #     {"role": "file-scout", "task": "List all config files"}
    # ])

Built-in roles:
- file-scout: Read-only research, searches files and returns digest
- scheduler: Calendar operations (list, find slots, create events)
"""

from agent.subagents.orchestrator import (
    SubAgentContext,
    execute_subagent,
    execute_subagents_parallel,
    make_delegate_parallel_tool,
    make_delegate_tool,
)
from agent.subagents.roles import (
    BUILTIN_ROLES,
    SubAgentRole,
    get_role,
    list_roles,
    make_file_scout_role,
    make_scheduler_role,
)

__all__ = [
    "BUILTIN_ROLES",
    "SubAgentContext",
    "SubAgentRole",
    "execute_subagent",
    "execute_subagents_parallel",
    "get_role",
    "list_roles",
    "make_delegate_parallel_tool",
    "make_delegate_tool",
    "make_file_scout_role",
    "make_scheduler_role",
]
