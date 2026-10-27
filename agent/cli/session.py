"""Session state shared by the REPL and the slash commands that act on it.

A Session is the full lifetime of one agent process (see CONTEXT.md): the conversation, the
Agent Mode in effect, the tools available, and the terminal it renders to. The REPL owns
one; commands mutate it. Keeping that state in a single object is what lets a command like
`/clear` or `/mode` change the session without the command package importing the REPL.

The system prompt loader lives here rather than in repl.py for the same reason — both the
REPL (session start) and `/mode` (session restart) need it, and neither should import the
other.

See also: agent.cli.commands (acts on a Session), agent.cli.repl (owns one).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
)

from agent.cli.ui import ConsoleUI
from agent.tools import ToolRegistry

if TYPE_CHECKING:
    from agent.mcp.manager import MCPManager


def load_system_prompt(mode: str = "default") -> str:
    """Load the system prompt for an Agent Mode from agent/prompts/{mode}.md.

    Modes are named prompt files; a missing file is an error rather than a
    silent fallback, so a typo surfaces immediately instead of running the wrong agent.

    mode: Agent Mode name, matching a file in agent/prompts/.

    Returns the prompt text, stripped.

    Raises FileNotFoundError if no prompt file exists for the mode.

    Example:
        prompt = load_system_prompt("default")

    See also: system_message, Session.switch_mode.
    """
    base_dir = Path(__file__).resolve().parent.parent / "prompts"
    prompt_file = base_dir / f"{mode}.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"System prompt for mode '{mode}' not found at {prompt_file}")
    with prompt_file.open("r", encoding="utf-8") as f:
        return f.read().strip()


def available_modes() -> list[str]:
    """List the Agent Modes that have a prompt file, sorted by name.

    Reads agent/prompts/ rather than a hardcoded list, so a new prompt file is offered by
    /mode completion the moment it is added.

    Returns the mode names, without the .md suffix.

    See also: load_system_prompt.
    """
    base_dir = Path(__file__).resolve().parent.parent / "prompts"
    return sorted(path.stem for path in base_dir.glob("*.md"))


def system_message(
    mode: str = "default",
    workspace_root: str | None = None,
    memory_store = None,
) -> ChatCompletionSystemMessageParam:
    """Build the opening system message for an Agent Mode with rules and memories.

    Args:
        mode: Agent Mode name (determines base prompt)
        workspace_root: Path to workspace for loading rules (optional)
        memory_store: Optional MemoryStore for loading pinned memories

    Returns:
        System message with mode prompt, rules, and pinned memories appended
    """
    from agent.context.rules import format_rules_section, load_rules
    from agent.memory.policy import (
        PINNED_MAX_CHARS,
        PINNED_MAX_COUNT,
        format_pinned_injection,
    )

    # Load base mode prompt
    prompt = load_system_prompt(mode)

    # Append rules if workspace is provided
    if workspace_root:
        rules = load_rules(workspace_root)
        rules_section = format_rules_section(rules)
        if rules_section:
            prompt = prompt + rules_section
    
    # Append pinned memories if store is provided
    if memory_store is not None:
        try:
            pinned_memories = memory_store.get_pinned(
                max_count=PINNED_MAX_COUNT,
                max_chars=PINNED_MAX_CHARS,
            )
            stats = memory_store.get_stats()
            memory_section = format_pinned_injection(pinned_memories, stats)
            if memory_section:
                prompt = prompt + memory_section
        except Exception as e:
            # Memory failure should not crash startup
            import logging
            logging.getLogger(__name__).warning(f"Failed to load pinned memories: {e}")

    return ChatCompletionSystemMessageParam(role="system", content=prompt)


@dataclass
class Session:
    """Session holds everything one interactive run of the agent can read or change.

    Commands receive a Session and mutate it in place; the REPL reads `running` to decide
    whether to keep looping, and passes `messages` to process_turn_fn each turn.

    messages: the live conversation, mutated in place so process_turn_fn keeps its history.
    mode: Agent Mode name currently in effect.
    model: model name the dispatcher is bound to, shown in the banner and by /model.
    tools: registry the loop dispatches tool calls against, listed by /tools.
    ui: terminal renderer commands write through.
    mcp_manager: MCP server lifecycle manager (Week 2); None if no MCP integration.
    running: False ends the session after the current command.

    Example:
        session = Session(messages=[system_message("default")], mode="default",
                          model="qwen-agentworld-35b-a3b", tools=registry, ui=ConsoleUI())

    See also: agent.cli.commands.CommandRegistry.dispatch.
    """

    messages: list[ChatCompletionMessageParam]
    mode: str
    model: str
    tools: ToolRegistry
    ui: ConsoleUI
    config: dict[str, Any] = field(default_factory=dict)
    workspace_root: str | None = field(default=None)
    loaded_skills: set[str] = field(default_factory=set)
    mcp_manager: MCPManager | None = field(default=None)
    memory_store: Any = field(default=None)
    vector_store: Any = field(default=None)  # Semantic index for promoted summaries; None if off
    permission_module: Any = field(default=None)  # Permission module for /permissions command
    session_id: str = field(default="unknown")
    running: bool = field(default=True)

    def reset_history(self) -> None:
        """Forget the conversation, keeping only the system prompt for the current mode.

        The list is mutated in place because process_turn_fn holds the same reference.
        """
        self.messages[:] = [system_message(self.mode, self.workspace_root, self.memory_store)]

    def switch_mode(self, mode: str) -> None:
        """Adopt another Agent Mode and restart the conversation under its prompt.

        mode: Agent Mode name.

        Raises FileNotFoundError if the mode has no prompt file; the session is left
        untouched in that case, so a typo cannot destroy the conversation.
        """
        message = system_message(mode, self.workspace_root, self.memory_store)  # raises before any state changes
        self.mode = mode
        self.messages[:] = [message]
