"""Sub-agent configuration and role definitions.

Defines sub-agent roles with permissions, limits, and descriptions.
Provides two built-in specialized configurations: file-scout and scheduler.

Prompts are loaded from agent/prompts/subagents/ to separate configuration
from code and enable easier prompt iteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _load_subagent_prompt(filename: str) -> str:
    """Load a sub-agent prompt from agent/prompts/subagents/{filename}.
    
    Prompts live in the prompts tree (not roles.py) so they can be edited
    without modifying Python code. Follows same pattern as agent mode prompts.
    
    Args:
        filename: Prompt filename (e.g., "file_scout_description.md")
        
    Returns:
        Prompt content, stripped
        
    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_file = Path(__file__).resolve().parent.parent / "prompts" / "subagents" / filename
    if not prompt_file.exists():
        raise FileNotFoundError(f"Sub-agent prompt not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8").strip()


@dataclass
class SubAgentRole:
    """Configuration for a specialized sub-agent role.
    
    Attributes:
        name: Unique identifier for this role (e.g. "researcher", "file-scout")
        description: What this sub-agent is good at (shown to parent agent)
        allowlist: Tools this sub-agent is allowed to use
        max_iterations: Maximum ReAct loop iterations
        max_result_chars: Maximum chars in the condensed result returned to parent
        system_prompt_suffix: Optional additional instructions for this role
    """
    name: str
    description: str
    allowlist: list[str] = field(default_factory=list)
    max_iterations: int = 8
    max_result_chars: int = 2000
    system_prompt_suffix: str = ""


# Built-in sub-agent roles

def make_file_scout_role() -> SubAgentRole:
    """Create a file-scout sub-agent role (read-only research).
    
    Tools: read_file, list_files, search_text, glob, grep
    Tier: read-only
    Limits: 8 iterations, 2000-char result
    
    Use case: Opens many files and returns a short digest of references.
    Benefit: Search costs 15-30 observations. Run in parent, those sit in context
             for the rest of the session. Run in child, parent pays for one 2000-char
             observation.
    
    Returns:
        SubAgentRole configuration
    """
    return SubAgentRole(
        name="file-scout",
        description=_load_subagent_prompt("file_scout_description.md"),
        allowlist=["read_file", "list_files", "search_text", "glob", "grep"],
        max_iterations=8,
        max_result_chars=2000,
        system_prompt_suffix=_load_subagent_prompt("file_scout_instructions.md"),
    )


def make_scheduler_role() -> SubAgentRole:
    """Create a scheduler sub-agent role (calendar operations).
    
    Can check current time, list events, find slots, create events.
    Cannot delete events (enforced by absence in MCP server).
    
    Returns:
        SubAgentRole configuration
    """
    return SubAgentRole(
        name="scheduler",
        description=_load_subagent_prompt("scheduler_description.md"),
        allowlist=[
            # MCP calendar tools (with mcp_ prefix from registry)
            "mcp_calendar__current_datetime",
            "mcp_calendar__list_events",
            "mcp_calendar__find_free_slots",
            "mcp_calendar__create_event",
            "mcp_calendar__update_event",
            # Read-only built-in tools
            "read_file",
            "list_files",
        ],
        max_iterations=10,
        max_result_chars=1500,
        system_prompt_suffix=_load_subagent_prompt("scheduler_instructions.md"),
    )


def make_general_role() -> SubAgentRole:
    """Create a general-purpose sub-agent role (open-ended investigation).

    Where file-scout returns a digest of what it found, this role returns a
    conclusion — it reasons over the evidence before answering. Suited to
    running several at once on independent branches of one question.

    The allowlist holds only tools that resolve to ALLOW under permissions.yaml.
    Sub-agents cannot prompt the user, so an ASK tool is auto-denied at call
    time; listing one here would only buy a denial and a wasted
    iteration. That rules out the write tools, load_skill and the MCP tools
    under the current policy. update_todo_list is excluded for a second reason:
    it overwrites one shared .agent_todo.md, so parallel siblings would clobber
    each other's plans.

    Returns:
        SubAgentRole configuration
    """
    return SubAgentRole(
        name="general",
        description=_load_subagent_prompt("general_description.md"),
        allowlist=[
            "read_file",
            "list_files",
            "search_text",
            "glob",
            "grep",
            "search_memory",
        ],
        max_iterations=12,
        max_result_chars=3000,
        system_prompt_suffix=_load_subagent_prompt("general_instructions.md"),
    )


# Registry of available roles
BUILTIN_ROLES: dict[str, callable[[], SubAgentRole]] = {
    "file-scout": make_file_scout_role,
    "scheduler": make_scheduler_role,
    "general": make_general_role,
}


def get_role(name: str) -> SubAgentRole:
    """Get a sub-agent role by name.
    
    Args:
        name: Role name (e.g. "file-scout", "scheduler")
        
    Returns:
        SubAgentRole configuration
        
    Raises:
        ValueError: If role name is not recognized
    """
    if name not in BUILTIN_ROLES:
        available = ", ".join(BUILTIN_ROLES.keys())
        raise ValueError(f"Unknown sub-agent role '{name}'. Available: {available}")
    
    return BUILTIN_ROLES[name]()


def list_roles() -> list[str]:
    """List all available sub-agent role names.
    
    Returns:
        List of role names
    """
    return list(BUILTIN_ROLES.keys())
