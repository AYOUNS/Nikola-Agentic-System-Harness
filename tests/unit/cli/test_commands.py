"""Unit tests for agent.cli.commands (slash-command registry and built-ins)."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from agent.cli.commands import Command, CommandRegistry, make_default_command_registry
from agent.cli.commands.builtin import get_builtin_commands
from agent.cli.session import Session, system_message
from agent.cli.ui import ConsoleUI
from agent.tools import ToolRegistry, make_default_tool_registry


def _session(tools: ToolRegistry | None = None) -> tuple[Session, io.StringIO]:
    """Build a Session rendering into a buffer instead of a terminal."""
    buffer = io.StringIO()
    ui = ConsoleUI(console=Console(file=buffer, force_terminal=False, width=100), streams=True)
    session = Session(
        messages=[system_message("default")],
        mode="default",
        model="mock-model",
        tools=tools if tools is not None else make_default_tool_registry(),
        ui=ui,
    )
    return session, buffer


def test_is_command_distinguishes_commands_from_prompts() -> None:
    """Only a leading slash marks input as a command."""
    registry = make_default_command_registry()

    assert registry.is_command("/help")
    assert not registry.is_command("what is python")


def test_aliases_resolve_to_the_same_command() -> None:
    """Every declared alias reaches the command it was declared on."""
    registry = make_default_command_registry()

    assert registry.get("exit") is registry.get("quit")
    assert registry.get("help") is registry.get("?")
    assert registry.get("/help") is registry.get("help")  # leading slash tolerated


def test_help_lists_every_registered_command() -> None:
    """/help renders from the registry, so a new command needs no help-text edit."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/help", session)

    out = buffer.getvalue()
    for cmd in get_builtin_commands():
        assert cmd.name in out, f"{cmd.name} missing from /help"


def test_unknown_command_is_reported_not_raised() -> None:
    """A typo reports an error and leaves the session running."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/nope", session)

    assert "Unknown command '/nope'" in buffer.getvalue()
    assert session.running


def test_handler_failure_is_reported_not_raised() -> None:
    """A command that raises must not end the session."""
    session, buffer = _session()

    def boom(session: Session, args: str) -> None:
        raise RuntimeError("handler exploded")

    registry = CommandRegistry()
    registry.register(Command(name="boom", summary="fails", handler=boom))
    registry.dispatch("/boom", session)

    assert "handler exploded" in buffer.getvalue()
    assert session.running


def test_exit_command_stops_the_session() -> None:
    """/exit flips the flag the REPL loops on."""
    session, _ = _session()

    make_default_command_registry().dispatch("/exit", session)

    assert not session.running


def test_clear_command_keeps_only_the_system_prompt() -> None:
    """/clear forgets the conversation without restarting the process."""
    session, buffer = _session()
    session.messages.append({"role": "user", "content": "remember this"})  # type: ignore[arg-type]

    make_default_command_registry().dispatch("/clear", session)

    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "system"
    assert "cleared" in buffer.getvalue().lower()


def test_mode_command_switches_and_restarts_the_conversation() -> None:
    """/mode loads the new mode's prompt and drops the old conversation."""
    session, buffer = _session()
    session.messages.append({"role": "user", "content": "earlier turn"})  # type: ignore[arg-type]

    make_default_command_registry().dispatch("/mode default", session)

    assert session.mode == "default"
    assert len(session.messages) == 1
    assert "default" in buffer.getvalue()


def test_mode_command_rejects_unknown_mode_without_losing_history() -> None:
    """A bad mode name reports an error and leaves the session exactly as it was."""
    session, buffer = _session()
    session.messages.append({"role": "user", "content": "earlier turn"})  # type: ignore[arg-type]

    make_default_command_registry().dispatch("/mode no_such_mode_xyz", session)

    assert session.mode == "default"
    assert len(session.messages) == 2  # conversation survived
    assert "Error running /mode" in buffer.getvalue()


def test_mode_command_without_argument_reports_current_mode() -> None:
    """Bare /mode is informational rather than an error."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/mode", session)

    assert "Mode: default" in buffer.getvalue()


def test_tools_command_reads_the_live_registry() -> None:
    """/tools lists what the loop would actually dispatch against."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/tools", session)

    out = buffer.getvalue()
    assert "write_file" in out
    assert "search_text" in out


def test_tools_command_on_empty_registry() -> None:
    """An agent with no tools says so instead of printing an empty table."""
    session, buffer = _session(tools=ToolRegistry())

    make_default_command_registry().dispatch("/tools", session)

    assert "No built-in tools registered" in buffer.getvalue()


def test_model_command_reports_the_bound_model() -> None:
    """/model names the model the dispatcher was built with."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/model", session)

    assert "mock-model" in buffer.getvalue()


def test_unambiguous_abbreviation_runs_the_command() -> None:
    """Typing a unique prefix is enough, so commands work without Tab completion."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/he", session)

    assert "/clear" in buffer.getvalue()  # the /help table rendered


def test_ambiguous_abbreviation_lists_the_candidates() -> None:
    """A prefix matching several commands names them instead of guessing."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/m", session)

    out = buffer.getvalue()
    assert "/mode" in out
    assert "/model" in out


def test_abbreviation_carries_its_arguments() -> None:
    """An abbreviated command still receives what was typed after it."""
    session, _ = _session()
    seen: list[str] = []

    registry = CommandRegistry()
    registry.register(
        Command(name="deploy", summary="deploy", handler=lambda session, args: seen.append(args))
    )
    registry.dispatch("/dep staging", session)

    assert seen == ["staging"]


def test_exact_name_wins_over_being_a_prefix_of_another() -> None:
    """/mode must run, not report ambiguity, even though /model extends it."""
    session, buffer = _session()

    make_default_command_registry().dispatch("/mode default", session)

    assert "Mode switched" in buffer.getvalue()
    assert "matches" not in buffer.getvalue()


def test_custom_command_can_be_registered_and_override_a_builtin() -> None:
    """Registering is the whole extension procedure, including replacing a built-in."""
    session, _ = _session()
    registry = make_default_command_registry()
    seen: list[str] = []

    registry.register(
        Command(
            name="mode",
            summary="replaced",
            handler=lambda session, args: seen.append(args),
            usage="/mode <name>",
        )
    )
    registry.dispatch("/mode experimental", session)

    assert seen == ["experimental"]
    assert session.mode == "default"  # the built-in did not run
    assert next(c for c in registry.commands() if c.name == "mode").summary == "replaced"


@pytest.mark.parametrize("line, expected_args", [("/mode default", "default"), ("/mode   spaced  ", "spaced")])
def test_arguments_are_parsed_off_the_command_name(line: str, expected_args: str) -> None:
    """Everything after the first space is the argument string, stripped."""
    session, _ = _session()
    seen: list[str] = []

    registry = CommandRegistry()
    registry.register(
        Command(name="mode", summary="capture", handler=lambda session, args: seen.append(args))
    )
    registry.dispatch(line, session)

    assert seen == [expected_args]
