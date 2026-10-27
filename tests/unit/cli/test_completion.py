"""Unit tests for agent.cli.completion (Tab completion and prompt history)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.cli.commands import Command, CommandRegistry, make_default_command_registry
from agent.cli.completion import candidates, install, make_completer


@pytest.fixture
def registry() -> CommandRegistry:
    """The default command registry."""
    return make_default_command_registry()


def test_command_names_complete_from_a_prefix(registry: CommandRegistry) -> None:
    """Typing part of a command offers the commands that extend it."""
    assert candidates("/he", "/he", registry) == ["/help "]


def test_bare_slash_offers_every_command(registry: CommandRegistry) -> None:
    """A lone slash lists everything, which is how the commands are discovered."""
    offered = candidates("/", "/", registry)

    assert "/help " in offered
    assert "/tools " in offered
    assert len(offered) == len(registry.commands())


def test_arguments_complete_through_the_commands_own_callable(registry: CommandRegistry) -> None:
    """/mode completes Agent Modes, supplied by the completer declared on the command."""
    assert candidates("/mode d", "d", registry) == ["default"]


def test_commands_without_an_argument_completer_offer_nothing(registry: CommandRegistry) -> None:
    """A command that takes no argument must not offer command names as arguments."""
    assert candidates("/help some", "some", registry) == []


def test_unknown_command_arguments_offer_nothing(registry: CommandRegistry) -> None:
    """Arguments to a command that does not exist have nothing to complete."""
    assert candidates("/nope arg", "arg", registry) == []


def test_prose_never_completes(registry: CommandRegistry) -> None:
    """A prompt for the model is not a command line, so Tab stays silent."""
    assert candidates("what is python", "what", registry) == []


def test_custom_command_completions_are_offered() -> None:
    """A registered command's own completer drives Tab, with no change to this module."""
    registry = CommandRegistry()
    registry.register(
        Command(
            name="deploy",
            summary="deploy something",
            handler=lambda session, args: None,
            complete=lambda partial: [e for e in ("staging", "production") if e.startswith(partial)],
        )
    )

    assert candidates("/deploy s", "s", registry) == ["staging"]
    assert candidates("/dep", "/dep", registry) == ["/deploy "]


def test_completer_walks_matches_by_state(monkeypatch: pytest.MonkeyPatch, registry: CommandRegistry) -> None:
    """readline asks for match 0, 1, 2 … and expects None past the end."""
    readline = pytest.importorskip("readline")
    monkeypatch.setattr(readline, "get_line_buffer", lambda: "/m")

    complete = make_completer(registry)
    first = complete("/m", 0)
    second = complete("/m", 1)
    third = complete("/m", 2)

    assert {first, second, third} == {"/mcp ", "/mode ", "/model "}
    assert complete("/m", 3) is None


def test_install_configures_readline_and_survives_a_missing_history(tmp_path: Path) -> None:
    """Installing works on a fresh machine where no history file exists yet."""
    readline = pytest.importorskip("readline")
    previous = readline.get_completer()
    try:
        assert install(make_default_command_registry(), tmp_path / "absent" / "history") is True
        assert readline.get_completer() is not None
    finally:
        readline.set_completer(previous)


def test_history_is_written_to_the_configured_path(tmp_path: Path) -> None:
    """Prompt history survives the session, so up-arrow works on the next start."""
    readline = pytest.importorskip("readline")
    from agent.cli.completion import _save_history

    history = tmp_path / "nested" / "history"
    previous = readline.get_completer()
    try:
        install(make_default_command_registry(), history)
        readline.add_history("/tools")
        _save_history(history)
    finally:
        readline.set_completer(previous)

    assert history.exists()
    assert "/tools" in history.read_text()


def test_backend_probes_agree_with_the_running_interpreter() -> None:
    """The libedit probe only claims libedit when readline actually is that build."""
    from agent.cli.completion import is_available, is_libedit

    readline = pytest.importorskip("readline")

    assert is_available() is True
    assert is_libedit() == ("libedit" in (readline.__doc__ or ""))


def test_diagnostics_reports_the_facts_that_decide_completion() -> None:
    """The doctor names every input to the decision, so a report is actionable."""
    from agent.cli.completion import diagnostics

    report = diagnostics()

    assert set(report) >= {"python", "readline", "stdin is a tty", "TERM", "~/.inputrc"}
    assert report["readline"] in {"gnu", "libedit", "missing"}


def test_suggestion_points_at_the_closest_command(registry: CommandRegistry) -> None:
    """A near miss is corrected rather than merely rejected."""
    assert registry.suggest("hepl") == " Did you mean /help?"
    assert registry.suggest("zzzzzz") == " Try /help."
