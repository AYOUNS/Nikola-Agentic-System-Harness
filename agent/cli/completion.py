"""Tab completion and input history for the REPL prompt.

Wires the stdlib `readline` to the CommandRegistry so pressing Tab completes command names,
and each command completes its own arguments through the `complete` callable declared
beside its handler — the registry stays the single source for what exists and what it takes.

readline also gives the prompt line editing and a persistent history (up-arrow across
sessions) for free, which is why no third-party prompt library is pulled in.

Every entry point degrades quietly: readline is absent on some Python builds, and it needs
an interactive stdin — which is the real requirement, and a different question from whether
stdout can render colour. Neither case may stop a session from starting.

See also: agent.cli.commands.Command.complete, agent.cli.repl.run_repl.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable
from pathlib import Path

from agent.cli.commands import COMMAND_PREFIX, CommandRegistry


def is_libedit() -> bool:
    """Report whether readline is the libedit build shipped on some macOS Pythons.

    libedit accepts a different bind syntax, has none of GNU readline's `set` options, and
    does not understand the \001/\002 prompt markers, so callers must adapt.

    Returns True for libedit, False for GNU readline or when readline is unavailable.
    """
    try:
        import readline
    except ImportError:  # pragma: no cover - platform dependent
        return False
    return "libedit" in (getattr(readline, "__doc__", "") or "")


def is_available() -> bool:
    """Report whether readline can drive the prompt at all."""
    try:
        import readline  # noqa: F401
    except ImportError:  # pragma: no cover - platform dependent
        return False
    return True


DEFAULT_HISTORY_PATH = Path.home() / ".agent_harness_history"
"""Where the prompt history persists between sessions."""

HISTORY_LIMIT = 1000
"""Lines of history retained; keeps the file small enough to read on every start."""


def candidates(line: str, text: str, commands: CommandRegistry) -> list[str]:
    """Compute the completions offered for the current input.

    Only command lines complete: free prose is a prompt for the model, where suggesting
    command names would be noise. Before the first space the candidates are command names;
    after it they come from the command's own `complete` callable, so a command that takes
    no arguments simply offers nothing.

    line: the whole input buffer as typed so far.
    text: the word readline is completing.
    commands: registry to resolve names against.

    Returns the matching completions, sorted, each ready to be inserted.

    Example:
        candidates("/he", "/he", registry)  # -> ["/help "]

    See also: make_completer.
    """
    stripped = line.lstrip()
    if not stripped.startswith(COMMAND_PREFIX):
        return []

    name, separator, argument = stripped[len(COMMAND_PREFIX) :].partition(" ")
    if not separator:
        return sorted(
            f"{COMMAND_PREFIX}{command.name} "
            for command in commands.commands()
            if f"{COMMAND_PREFIX}{command.name}".startswith(text)
        )

    command = commands.get(name)
    if command is None or command.complete is None:
        return []
    return sorted(match for match in command.complete(argument.strip()) if match.startswith(text))


def make_completer(commands: CommandRegistry) -> Callable[[str, int], str | None]:
    """Build the readline completer callback for a registry.

    readline asks for match `state` 0, 1, 2 … until the callback returns None.

    commands: registry the completions are drawn from.

    Returns a callable suitable for readline.set_completer.

    See also: candidates, install.
    """
    import readline

    def complete(text: str, state: int) -> str | None:
        matches = candidates(readline.get_line_buffer(), text, commands)
        return matches[state] if state < len(matches) else None

    return complete


def install(commands: CommandRegistry, history_path: Path | None = None) -> bool:
    """Enable Tab completion and persistent history on the prompt.

    commands: registry driving the completions.
    history_path: file to read history from and append to. Defaults to
                  ~/.agent_harness_history; a path that cannot be read is ignored rather
                  than reported, since a missing history is not a failure worth a message.

    Returns True when readline was available and configured, False when it was not — the
    caller can start a session either way.

    Example:
        install(make_default_command_registry())

    See also: make_completer.
    """
    try:
        import readline
    except ImportError:  # pragma: no cover - platform dependent
        return False

    readline.set_completer(make_completer(commands))
    # Keep "/" inside the word being completed, so "/he" completes as one token.
    readline.set_completer_delims(" \t\n")

    if is_libedit():
        # macOS builds link libedit, which uses its own bind syntax and has no settings.
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
        # Without this, an ambiguous prefix does nothing visible on the first Tab and only
        # lists on the second — which reads as "Tab is broken". "/" has six candidates, so
        # that is the very first thing anyone tries.
        readline.parse_and_bind("set show-all-if-ambiguous on")
        readline.parse_and_bind("set completion-ignore-case on")

    path = history_path if history_path is not None else DEFAULT_HISTORY_PATH
    try:
        readline.read_history_file(path)
    except (OSError, ValueError):
        pass  # first run, or an unreadable file: start with empty history

    readline.set_history_length(HISTORY_LIMIT)
    atexit.register(_save_history, path)
    return True


def diagnostics() -> dict[str, str]:
    """Collect everything that decides whether Tab completion can work.

    Reports the interpreter, the readline build, whether the streams are interactive, and
    whether a personal ~/.inputrc is present — a custom inputrc is the usual reason Tab is
    bound to something other than completion.

    Returns an ordered mapping of check name to human-readable value.

    Example:
        for name, value in diagnostics().items():
            print(f"{name}: {value}")
    """
    import sys

    inputrc = Path.home() / ".inputrc"
    return {
        "python": sys.executable,
        "version": sys.version.split()[0],
        "readline": "missing" if not is_available() else ("libedit" if is_libedit() else "gnu"),
        "stdin is a tty": str(sys.stdin.isatty()),
        "stdout is a tty": str(sys.stdout.isatty()),
        "TERM": repr(__import__("os").environ.get("TERM")),
        "~/.inputrc": "present (may rebind Tab)" if inputrc.exists() else "absent",
        "history file": str(DEFAULT_HISTORY_PATH),
    }


def self_test() -> None:
    """Print diagnostics, then read one line so Tab can be tried in the real terminal.

    Run as: python -m agent.cli --doctor
    """
    from agent.cli.commands import make_default_command_registry

    for name, value in diagnostics().items():
        print(f"  {name:<16} {value}")

    installed = install(make_default_command_registry())
    print(f"  {'install()':<16} {installed}")
    if not installed:
        print("\nreadline is unavailable, so Tab cannot complete on this interpreter.")
        return

    print("\nType  /  then press Tab (the commands should list), then press Enter.")
    try:
        typed = input("You › ")
    except (EOFError, KeyboardInterrupt):
        print("\naborted")
        return
    print(f"\nthe prompt received: {typed!r}")
    if "\t" not in typed:
        print("\nNo tab character reached the prompt. If the command list did not appear,")
        print("your terminal is intercepting Tab before Python sees it:")
        print("  · VS Code — Ctrl+M toggles Tab focus mode; turn it off")
        print("  · tmux/screen — check any bind-key for Tab")
        print("Commands still work without Tab: any unambiguous prefix runs, so '/he' runs /help.")


def _save_history(path: Path) -> None:
    """Persist the prompt history, ignoring a read-only or missing home directory."""
    import readline

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(path)
    except (OSError, ValueError):  # pragma: no cover - depends on the filesystem
        pass
