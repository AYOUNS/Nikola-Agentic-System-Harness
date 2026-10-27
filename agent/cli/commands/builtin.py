"""The built-in slash commands.

Every command is declared once, here, by decorating its handler with @command. The
decorator carries the metadata that /help renders, so adding a command is a single edit:
write the handler, decorate it, done — no registry list, no help text, nothing else to
update.

Handlers take (session, args) and report through session.ui. Raising is safe: the registry
catches handler errors and reports them without ending the session.

See also: agent.cli.commands.CommandRegistry, agent.cli.session.Session.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from agent.cli.commands import (
    COMMAND_PREFIX,
    ArgumentCompleter,
    Command,
    CommandHandler,
)
from agent.cli.session import Session, available_modes

_BUILTINS: list[Command] = []


def command(
    name: str,
    summary: str,
    usage: str = "",
    aliases: tuple[str, ...] = (),
    complete: ArgumentCompleter | None = None,
) -> Callable[[CommandHandler], CommandHandler]:
    """Declare a built-in slash command, using the decorated function as its handler.

    The decorated function is returned unchanged, so it stays directly callable in tests.

    name: invocation name without the leading slash.
    summary: one-line description shown by /help.
    usage: argument shape shown by /help, e.g. "/mode <name>".
    aliases: alternative names for the same command.
    complete: optional callable offering Tab completions for the command's argument.

    Returns the decorator.

    Example:
        @command("clear", "Forget the conversation so far.")
        def clear_command(session: Session, args: str) -> None:
            session.reset_history()

    See also: get_builtin_commands.
    """

    def decorate(handler: CommandHandler) -> CommandHandler:
        _BUILTINS.append(
            Command(
                name=name,
                summary=summary,
                handler=handler,
                usage=usage,
                aliases=aliases,
                complete=complete,
            )
        )
        return handler

    return decorate


@command("help", "List the available commands.", aliases=("?", "h"))
def help_command(session: Session, args: str) -> None:
    """Show every registered command, rendered from the registry itself."""
    from agent.cli.commands import make_default_command_registry

    session.ui.table(
        "Commands",
        [(cmd.invocation, cmd.summary) for cmd in make_default_command_registry().commands()],
    )


@command("exit", "End the session.", aliases=("quit", "q"))
def exit_command(session: Session, args: str) -> None:
    """Stop the REPL after this command returns."""
    session.running = False


@command("clear", "Forget the conversation so far, keeping the current mode's prompt.")
def clear_command(session: Session, args: str) -> None:
    """Reset the conversation without restarting the process."""
    session.reset_history()
    session.ui.info("Conversation cleared.")


def _complete_mode(partial: str) -> list[str]:
    """Offer the Agent Modes that have a prompt file, matching what has been typed."""
    return [mode for mode in available_modes() if mode.startswith(partial)]


@command(
    "mode",
    "Switch Agent Mode, restarting the conversation under its prompt.",
    usage="/mode <name>",
    complete=_complete_mode,
)
def mode_command(session: Session, args: str) -> None:
    """Switch Agent Mode, or report the current one when called without an argument."""
    if not args:
        session.ui.info(f"Mode: {session.mode}. Usage: {COMMAND_PREFIX}mode <name>")
        return

    session.switch_mode(args)
    session.ui.info(f"Mode switched to '{args}'. Conversation restarted.")


@command("tools", "List built-in tools. Use /mcp to see MCP server tools.")
def tools_command(session: Session, args: str) -> None:
    """Show built-in tools only (not MCP tools - those are shown by /mcp)."""
    rows = []
    for schema in session.tools.get_schemas():
        function = dict(schema).get("function", {})
        name = str(function.get("name", "?"))
        # Skip MCP tools (those starting with mcp_)
        if name.startswith("mcp_"):
            continue
        rows.append((name, str(function.get("description", ""))))

    if not rows:
        session.ui.info("No built-in tools registered.")
        return
    
    session.ui.table("Built-in Tools", rows)
    
    # Hint about MCP tools if any exist
    mcp_count = sum(1 for s in session.tools.get_schemas()
                    if str(dict(s).get("function", {}).get("name", "")).startswith("mcp_"))
    if mcp_count > 0:
        session.ui.info(f"💡 {mcp_count} MCP tool(s) available. Use /mcp to see them.")


@command("model", "Show the model this session dispatches to.")
def model_command(session: Session, args: str) -> None:
    """Report the bound model, which is fixed for the life of the session."""
    session.ui.info(f"Model: {session.model} · mode: {session.mode}")


_DOCSTRING_SECTION = re.compile(
    r"^\s*(Args|Arguments|Returns|Raises|Yields|Examples?|Notes?):",
    re.MULTILINE,
)
"""Start of a docstring section.

Tool descriptions arrive as whole docstrings, but the listing wants only the summary — the
parameters are already described by the tool's own schema.
"""


def _summarize(description: str) -> str:
    """Reduce a tool description to a single-line summary.

    Drops any Args:/Returns:/... section and collapses the docstring's source indentation,
    which would otherwise render as ragged whitespace inside a table cell.
    """
    text = description.strip()
    section = _DOCSTRING_SECTION.search(text)
    if section:
        text = text[: section.start()]
    return " ".join(text.split())


@command("mcp", "Show status of connected MCP servers and their tools.")
def mcp_command(session: Session, args: str) -> None:
    """Display MCP servers with the tools each one contributes to the registry."""
    # Check if MCP is configured
    if not hasattr(session, "mcp_manager") or session.mcp_manager is None:
        session.ui.info("No MCP integration configured.")
        return

    status = session.mcp_manager.get_server_status()

    if not status:
        session.ui.info("No MCP servers configured (.nikola/mcp.json not found or empty).")
        return

    # Group registered tools by the server prefix in mcp_<server>__<tool>. Counting what
    # actually reached the registry keeps the tally honest if a tool fails to register.
    tools_by_server: dict[str, list[tuple[str, str]]] = {name: [] for name in status}
    for schema in session.tools.get_schemas():
        function = dict(schema).get("function", {})
        name = str(function.get("name", ""))
        if not name.startswith("mcp_"):
            continue
        prefix, separator, tool_name = name.partition("__")
        server_name = prefix[len("mcp_") :] if separator else name
        summary = _summarize(str(function.get("description", "")))
        tools_by_server.setdefault(server_name, []).append((tool_name or name, summary))

    servers = []
    for server_name, info in status.items():
        if info["disconnected"]:
            state = "failed"
        elif info["connected"]:
            state = "connected"
        else:
            state = "disconnected"
        
        # Get error message for failed servers
        error = info.get("error")
        
        servers.append((server_name, state, tools_by_server.get(server_name, []), error))

    session.ui.mcp_overview(servers)


@command("context", "Show context window budget and token usage.")
def context_command(session: Session, args: str) -> None:
    """Show the current context budget breakdown."""
    from agent.context import get_budget_info

    # Get current messages and schemas
    messages = session.messages
    schemas = session.tools.get_schemas()
    config = session.config

    # Get budget info
    info = get_budget_info(messages, schemas, config)

    # Prepare rows for the table
    rows = [
        ("Window size", f"{info['window_tokens']:,} tokens"),
        ("Reserved for output", f"{info['reserve_output']:,} tokens"),
        ("Available for input", f"{info['available']:,} tokens"),
        ("", ""),  # Blank row for separation
        ("Current usage", f"{info['current_tokens']:,} tokens"),
        ("Remaining", f"{info['remaining']:,} tokens"),
    ]

    session.ui.table("Context Budget", rows)

    # Utilization reads as a meter rather than a table row: the fill shows how close the
    # window is to full without the reader comparing two numbers.
    session.ui.usage_bar(
        "Utilization",
        info["utilization_pct"],
        f"{info['current_tokens']:,} / {info['available']:,} tokens",
    )

    # Add hint if getting close to limit
    if info["utilization_pct"] >= 80:
        session.ui.hint(
            "Context is nearing capacity. Consider using /clear to start fresh."
        )


def _summarize_conversation(session: Session, history: list, instructions: str = "") -> str | None:
    """Return an LLM summary of history, or None if one could not be produced.

    Every failure path (no model configured, an API error, an empty completion) is reported
    through the UI and returns None, so the caller can leave the conversation untouched
    rather than replacing it with nothing.
    """
    if not session.config.get("llm", {}).get("model"):
        session.ui.hint("No model configured; cannot summarize.")
        return None

    from agent.context import summarize_messages
    from agent.llm.client import make_client
    from agent.llm.dispatcher import make_chat_completions_effect

    context_config = session.config.get("context", {})
    try:
        dispatch = make_chat_completions_effect(make_client(session.config), session.config)
        summary = summarize_messages(
            history,
            dispatch,
            max_tokens=context_config.get("summary_max_tokens", 1500),
            instructions=instructions,
            reasoning_effort=context_config.get("summary_reasoning_effort", "none"),
        )
    except Exception as error:
        session.ui.hint(f"Could not summarize ({error}); the conversation is unchanged.")
        return None

    if not summary:
        session.ui.hint("The summarizer returned nothing; the conversation is unchanged.")
        return None
    return summary


@command(
    "compact",
    "Replace the conversation with a summary of it, freeing context.",
    usage="/compact [what to keep]",
)
def compact_command(session: Session, args: str) -> None:
    """Condense the whole conversation into a single summary message.

    Unconditional, unlike the loop's automatic compaction: the point of running it by hand
    is to free room now, so it does not first check whether the conversation already fits.

    args: optional free text steering what the summary should preserve, e.g.
          "/compact keep the config.yaml findings".
    """
    from agent.context import estimate_tokens, get_budget_info, summary_message

    schemas = session.tools.get_schemas()
    chars_per_token = session.config.get("context", {}).get("chars_per_token", 3.5)

    def estimate_fn(msgs: list) -> int:
        return estimate_tokens(msgs, schemas, chars_per_token=chars_per_token)

    # The mode's system prompt is instruction, not conversation, so it survives verbatim.
    leading_system = (
        1 if session.messages and session.messages[0].get("role") == "system" else 0
    )
    history = session.messages[leading_system:]
    if not history:
        session.ui.info("Nothing to compact — the conversation is empty.")
        return

    before = estimate_fn(session.messages)
    summary = _summarize_conversation(session, history, args.strip())
    if summary is None:
        return

    # Replaced only once a summary exists, so a failed call never costs the conversation.
    session.messages[:] = [*session.messages[:leading_system], summary_message(summary)]

    # Persist the summary to long-term memory so it survives the session end.
    # This is the compaction → long-term memory promotion path (spec §2).
    if session.memory_store is not None:
        from agent.memory import persist_summary_to_memory
        persist_summary_to_memory(
            summary,
            session.session_id,
            session.memory_store,
            session.vector_store,
        )

    # A short conversation can summarize to something longer than itself, so the saving is
    # reported as a gain or a cost rather than as a negative number of tokens freed.
    freed = before - estimate_fn(session.messages)
    change = (
        f"freed ~{freed:,} tokens"
        if freed > 0
        else f"~{-freed:,} tokens larger than what it replaced"
    )
    session.ui.info(f"Compacted {len(history)} messages into a summary, {change}.")

    info = get_budget_info(session.messages, schemas, session.config)
    session.ui.usage_bar(
        "Utilization",
        info["utilization_pct"],
        f"{info['current_tokens']:,} / {info['available']:,} tokens",
    )


@command("rules", "List loaded user rules and their token cost.")
def rules_command(session: Session, args: str) -> None:
    """Show all rules loaded from workspace/.nikola/rules/."""
    from agent.context.rules import count_rules_tokens, load_rules

    if not session.workspace_root:
        session.ui.info("No workspace configured. Rules require a workspace root.")
        return

    rules = load_rules(session.workspace_root)

    if not rules:
        session.ui.info("No rules loaded. Add .md files to workspace/.nikola/rules/ to define rules.")
        return

    # Prepare table rows
    rows = []
    for rule in rules:
        desc = rule.description if rule.description else "(no description)"
        rows.append((rule.name, desc))

    session.ui.table("Loaded Rules", rows)

    # Show token cost
    token_count = count_rules_tokens(rules)
    session.ui.info(f"Rules consume ~{token_count:,} tokens from the context budget.")



@command("skills", "List or load skills.", usage="/skills [list|load <name>]")
def skill_command(session: Session, args: str) -> None:
    """List available skills or manually load a specific skill."""
    from agent.skills.loader import discover_skills
    from agent.skills.tool import load_skill_tool

    # Parse subcommand
    parts = args.strip().split(maxsplit=1)
    subcommand = parts[0] if parts else "list"

    if subcommand == "list":
        # List all available skills
        skills = discover_skills(session.workspace_root)

        if not skills:
            session.ui.info("No skills available.")
            return

        rows = []
        for skill in sorted(skills.values(), key=lambda s: s.name):
            status = " [loaded]" if skill.name in session.loaded_skills else ""
            rows.append((skill.name + status, skill.description))

        session.ui.table("Available Skills", rows)

        if session.loaded_skills:
            session.ui.info(
                f"Loaded: {', '.join(sorted(session.loaded_skills))}"
            )

    elif subcommand == "load":
        # Manual load
        if len(parts) < 2:
            session.ui.info("Usage: /skills load <name>")
            return

        skill_name = parts[1]
        result = load_skill_tool(skill_name, session.loaded_skills, session.workspace_root)

        # Show the result
        if result.startswith("Error:"):
            session.ui.error(result)
        elif result.startswith("[Note:"):
            session.ui.info(result)
        else:
            # Success - show confirmation
            session.ui.info(f"Loaded skill '{skill_name}'")

    else:
        session.ui.info("Usage: /skills [list|load <name>]")


@command("permissions", "Manage session permission cache.", usage="/permissions [clear|status]")
def permissions_command(session: Session, args: str) -> None:
    """Manage the session-level permission cache.
    
    Subcommands:
      status - Show cached permission decisions
      clear  - Clear all cached decisions (NOT IMPLEMENTED - cache is internal to PermissionModule)
    """
    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0] if parts else "status"
    
    # Get permission module from session
    permission_module = getattr(session, 'permission_module', None)
    
    if permission_module is None:
        session.ui.info("Permission module not available in this session.")
        return
    
    if subcommand == "status":
        # Show cache status
        stats = permission_module.stats()
        
        if stats["total_cached"] == 0:
            session.ui.info("No cached permission decisions for this session.")
            return
        
        session.ui.info(f"📋 Session Permission Cache: {stats['total_cached']} tool(s)")
        session.ui.info(f"  ✓ Always allowed: {stats['allowed_count']} tool(s)")
        session.ui.info(f"  ✗ Always denied: {stats['denied_count']} tool(s)")
        session.ui.hint("Cache clearing not yet supported")
    
    elif subcommand == "clear":
        session.ui.info("⚠ Cache clearing not yet implemented.")
        session.ui.hint("Restart the session to clear all cached decisions.")
    
    else:
        session.ui.info("Usage: /permissions [status|clear]")
        session.ui.info("  status - Show cached decisions")
        session.ui.info("  clear  - Clear all cached decisions")




def get_builtin_commands() -> list[Command]:
    """Return every command declared in this module, in declaration order.

    Returns the list the default registry is built from.

    See also: agent.cli.commands.make_default_command_registry.
    """
    return list(_BUILTINS)
