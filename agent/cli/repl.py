"""REPL runner and turn execution for the agent harness CLI.

Provides run_repl for interactive and one-shot turn processing.

Typed input is one of two things: a slash command, which acts on the Session (see
agent.cli.commands), or a prompt, which becomes a Turn. Interactive sessions on a terminal
also get Tab completion and history from agent.cli.completion. Everything else is delegated —
session state to agent.cli.session, presentation to agent.cli.ui — so this module only
routes input and drives turn boundaries.

Presentation always goes through a ConsoleUI. When the loop renders answers as
they generate (ui.streams, meaning ui.sink was wired in as make_process_turn_fn's
on_output), run_repl leaves the answer to it rather than printing it a second time.

See also: agent.cli.session.Session, agent.cli.commands.make_default_command_registry.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openai import APIError
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionUserMessageParam,
)

from agent.cli.commands import CommandRegistry, make_default_command_registry
from agent.cli.completion import install as install_completion
from agent.cli.session import Session, load_system_prompt, system_message
from agent.cli.ui import ConsoleUI
from agent.tools import ToolRegistry, make_default_tool_registry

if TYPE_CHECKING:
    from agent.mcp.manager import MCPManager

__all__ = ["load_system_prompt", "run_repl"]


async def run_repl(
    process_turn_fn: Callable[[list[ChatCompletionMessageParam]], any],
    one_shot_prompt: str | None = None,
    mode: str = "default",
    ui: ConsoleUI | None = None,
    model: str | None = None,
    tools: ToolRegistry | None = None,
    commands: CommandRegistry | None = None,
    mcp_manager: MCPManager | None = None,
    config: dict[str, Any] | None = None,
    loaded_skills: set[str] | None = None,
    memory_store: Any = None,
    vector_store: Any = None,
    permission_module: Any = None,
    session_id: str = "unknown",
) -> None:
    """Execute either a one-shot turn or an interactive REPL session.

    Interactive input starting with "/" is dispatched to the command registry; anything
    else is sent to the model as a Turn. A one-shot prompt may be either.

    process_turn_fn: the injected async turn processor.
    one_shot_prompt: if provided, processes exactly one prompt or command and returns.
    mode: Agent Mode determining the system prompt to load.
    ui: ConsoleUI owning every write to the terminal. Defaults to one that prints answers
        itself, which is correct when no sink was wired into the loop.
    model: model name shown in the banner and by /model.
    tools: registry listed by /tools. Defaults to the standard filesystem tools.
    commands: slash-command registry. Defaults to every built-in command.
    mcp_manager: MCP server manager; None if no MCP integration.
    config: application configuration dict (for context budgeting, etc.).
    loaded_skills: set tracking which skills have been loaded.
    memory_store: MemoryStore instance for long-term memory; None if disabled.
    vector_store: VectorStore instance; None when semantic search is unavailable. Carried
        on the Session so /compact indexes the summary it promotes, exactly as the loop's
        own compaction does.
    session_id: unique ID for this session, threaded into memory writes.

    Example:
        await run_repl(process_turn_fn, mode="default", ui=ConsoleUI(), model="gemma4-31b-it")

    See also: agent.cli.commands.CommandRegistry.dispatch.
    """
    commands = commands if commands is not None else make_default_command_registry()
    ui = ui if ui is not None else ConsoleUI(streams=False, command_registry=commands)
    
    # Get workspace root for rules loading
    workspace_root = config.get("workspace") if config else None
    
    session = Session(
        messages=[system_message(mode, workspace_root, memory_store)],
        mode=mode,
        model=model or "unknown",
        tools=tools if tools is not None else make_default_tool_registry(),
        ui=ui,
        config=config if config is not None else {},
        workspace_root=workspace_root,
        loaded_skills=loaded_skills if loaded_skills is not None else set(),
        mcp_manager=mcp_manager,
        memory_store=memory_store,
        vector_store=vector_store,
        permission_module=permission_module,
        session_id=session_id,
    )

    async def run_turn(prompt: str) -> None:
        """Send one prompt to the model and render whatever comes back.

        Before sending, retrieves the top-3 long-term memories relevant to this
        prompt and prepends them so the LLM has cross-session context automatically.
        This implements the per-turn read policy stated in agent.memory.policy.
        """
        # Per-turn retrieval: inject relevant long-term memories before the user
        # message (spec §2: "consulted on demand … just-in-time").
        content = prompt
        if session.memory_store is not None:
            try:
                from agent.memory.policy import (
                    PER_TURN_RETRIEVAL_LIMIT,
                    format_per_turn_retrieval,
                )
                relevant = session.memory_store.search_keyword(
                    prompt, limit=PER_TURN_RETRIEVAL_LIMIT
                )
                section = format_per_turn_retrieval(relevant)
                if section:
                    content = section + prompt
            except Exception:
                pass  # retrieval failure must never break a turn

        session.messages.append(
            ChatCompletionUserMessageParam(
                role="user",
                content=content,
            )
        )
        ui.begin_turn()
        try:
            answer = await process_turn_fn(session.messages)
        except APIError as exc:
            # Remove the user message we just appended so the broken turn is not replayed.
            session.messages.pop()
            ui.finish_turn()
            # This is a turn-ending error — use the boxed error sink
            error_msg = f"API error ({exc.status_code}): {exc.message}"
            remedy = "Check your connection and API key, then retry."
            ui.sink("error", f"{error_msg}|{remedy}")
            return
        if not ui.streams:
            ui.print_answer(answer)
        ui.finish_turn()

    async def handle(text: str) -> None:
        """Route one line of input to the command registry or to the model."""
        if commands.is_command(text):
            commands.dispatch(text, session)
        else:
            await run_turn(text)

    if one_shot_prompt is not None:
        # --prompt is used both ways: read by a person at a terminal, and piped into
        # something that parses the answer. Only the first wants chrome, and rich already
        # knows which is which, so the banner follows is_terminal rather than being
        # unconditionally suppressed.
        if ui.console.is_terminal:
            ui.banner(mode=session.mode, model=session.model)
        await handle(one_shot_prompt)
        return

    ui.banner(mode=session.mode, model=session.model)
    # readline needs an interactive stdin — which is the actual requirement, and not the
    # same question as whether rich thinks stdout can render colour.
    if sys.stdin.isatty():
        if install_completion(commands):
            # Hint is now shown in rprompt, no need to print it here
            pass
        else:
            ui.info(f"Tab completion unavailable: {sys.executable} has no readline module.")
    while session.running:
        try:
            user_input = ui.prompt()
            if not user_input:
                continue
            # Bare exit/quit predate the command prefix and stay supported.
            await handle(f"/{user_input}" if user_input.lower() in ("exit", "quit") else user_input)

        except (KeyboardInterrupt, EOFError):
            ui.goodbye("Exiting agent session.")
            return

    ui.goodbye()
