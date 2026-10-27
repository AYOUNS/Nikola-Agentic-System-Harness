"""CommandRegistry for the REPL's slash commands.

A slash command is anything the user types starting with "/" — it acts on the Session
instead of being sent to the model. Registration mirrors ToolRegistry: declarations come
from a `get_*` function and a `make_default_*_registry()` factory wires them up, so there
is exactly one place that decides which commands exist.

Each command's metadata (name, summary, usage, aliases) is declared once, on the handler
itself via the @command decorator in `builtin.py`. `/help` renders from the registry, so a
new command appears in help automatically — there is no second list to keep in sync.

Names may be abbreviated to any unambiguous prefix ("/he" runs /help), and an ambiguous one
lists the candidates. That makes every command reachable by typing alone, which matters
because Tab is not always delivered to the process — some terminals bind it to focus.

To add a command: write a handler in agent/cli/commands/builtin.py and decorate it. That
is the whole procedure; nothing else needs editing.

See also: agent.cli.commands.builtin, agent.cli.session.Session, agent.tools.ToolRegistry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import get_close_matches

from agent.cli.session import Session

CommandHandler = Callable[[Session, str], None]
"""A handler receives the Session and the raw argument string ("" when none were given)."""

ArgumentCompleter = Callable[[str], list[str]]
"""Maps the partial argument typed so far to the completions Tab should offer."""

COMMAND_PREFIX = "/"
"""The single character that distinguishes a command from a prompt."""


@dataclass(frozen=True)
class Command:
    """Command is one slash command and everything the REPL knows about it.

    name: invocation name without the leading slash.
    summary: one-line description, shown by /help.
    handler: callable invoked as handler(session, args).
    usage: argument shape shown by /help, e.g. "/mode <name>". Defaults to the bare name.
    aliases: alternative names accepted for the same command.
    complete: optional callable mapping the partial argument typed so far to the possible
              completions, used by Tab. None means the command takes no completable
              argument.
    """

    name: str
    summary: str
    handler: CommandHandler
    usage: str = ""
    aliases: tuple[str, ...] = field(default=())
    complete: ArgumentCompleter | None = None

    @property
    def invocation(self) -> str:
        """How the command is typed, for display in /help."""
        return self.usage or f"{COMMAND_PREFIX}{self.name}"


class CommandRegistry:
    """CommandRegistry maps typed names, including aliases, to Command definitions."""

    def __init__(self) -> None:
        """Initialize an empty CommandRegistry."""
        self._commands: dict[str, Command] = {}
        self._by_name: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        """Register a command under its name and every alias.

        command: the Command to add. Re-registering a name replaces it, which lets a
                 caller override a built-in without editing this package.
        """
        self._by_name[command.name] = command
        for key in (command.name, *command.aliases):
            self._commands[key] = command

    def get(self, name: str) -> Command | None:
        """Look up a command by name or alias, returning None when unknown."""
        return self._commands.get(name.lstrip(COMMAND_PREFIX).lower())

    def commands(self) -> list[Command]:
        """Return the distinct registered commands, sorted by name, for /help."""
        return sorted(self._by_name.values(), key=lambda command: command.name)

    def prefix_matches(self, name: str) -> list[Command]:
        """Return the commands whose name starts with a typed abbreviation.

        name: the abbreviation, without its prefix.

        Returns the matching commands, sorted by name; empty when nothing starts with it.
        """
        if not name:
            return []
        return [command for command in self.commands() if command.name.startswith(name.lower())]

    def suggest(self, name: str) -> str:
        """Build a hint for a name that did not resolve.

        name: the unknown command name, without its prefix.

        Returns " Did you mean /x?" when something is close, otherwise a pointer to /help.
        """
        close = get_close_matches(name, list(self._commands), n=1)
        if close:
            return f" Did you mean {COMMAND_PREFIX}{close[0]}?"
        return f" Try {COMMAND_PREFIX}help."

    def is_command(self, text: str) -> bool:
        """Report whether typed input should be treated as a command rather than a prompt."""
        return text.startswith(COMMAND_PREFIX)

    def dispatch(self, text: str, session: Session) -> None:
        """Parse and run one typed command line.

        Unknown commands and handler failures are reported through the session UI rather
        than raised, so a mistyped command never ends the session.

        text: the raw line, including the leading slash.
        session: the Session the command may read and mutate.

        Example:
            registry.dispatch("/mode default", session)
        """
        name, _, args = text[len(COMMAND_PREFIX) :].strip().partition(" ")
        if not name:
            name = "help"  # a bare slash asks what the commands are

        command = self.get(name)
        if command is None:
            # Fall back to abbreviation: an unambiguous prefix runs, an ambiguous one lists.
            # This keeps every command reachable when Tab is unavailable or intercepted.
            # Require full match for "skills" - no abbreviation to "skill"
            if name == "skill":
                session.ui.error(f"Unknown command '{COMMAND_PREFIX}{name}'.{self.suggest(name)}")
                return
            matches = self.prefix_matches(name)
            if len(matches) == 1:
                command = matches[0]
            elif matches:
                listed = ", ".join(f"{COMMAND_PREFIX}{match.name}" for match in matches)
                session.ui.info(f"'{COMMAND_PREFIX}{name}' matches: {listed}")
                return
            else:
                session.ui.error(f"Unknown command '{COMMAND_PREFIX}{name}'.{self.suggest(name)}")
                return

        try:
            command.handler(session, args.strip())
        except Exception as err:
            session.ui.error(f"Error running {COMMAND_PREFIX}{command.name}: {err}")


def make_default_command_registry() -> CommandRegistry:
    """Create a CommandRegistry populated with every built-in command.

    Returns a registry holding the commands declared in agent.cli.commands.builtin.

    Example:
        commands = make_default_command_registry()
        commands.dispatch("/help", session)

    See also: agent.cli.commands.builtin.get_builtin_commands.
    """
    from agent.cli.commands.builtin import get_builtin_commands

    registry = CommandRegistry()
    for command in get_builtin_commands():
        registry.register(command)
    return registry
