"""Terminal presentation for the agent harness CLI.

Simple, clean UI using only Rich library.
Everything the ReAct loop produces reaches the screen through a single sink — (kind, text)
— so the loop owns no formatting and this module owns every escape sequence.

Rendering rules:
  - "reasoning": chain-of-thought tokens drive a spinner with current thought tail
  - "tool": one line per call and one per observation
  - "answer": streamed verbatim with soft wrapping
  - "error": boxed turn-ending errors (red border)
  - "permission": boxed permission prompts (amber border)

Sub-agents are the exception to the one-line-per-tool rule: a delegation can
run for a minute behind a single tool call, so it gets a live card instead
(agent.cli.subagent_view), fed by hook events rather than by the sink.

Visual design follows the sleek/minimal spec with fixed color palette.

See also: agent.loop.make_process_turn_fn (produces the sink events), agent.cli.repl.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from agent.cli.subagent_view import SubAgentLiveView

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


logger = logging.getLogger(__name__)


def _round_prompt_toolkit_corners() -> None:
    """Give prompt_toolkit's Frame the same rounded corners as the rich panels.

    The input box is drawn by prompt_toolkit (``show_frame``) while the
    permission and error boxes are drawn by rich, and the two libraries ship
    different default corners — square vs rounded. Since all three appear in
    the same transcript, the corners have to agree.

    ``Frame`` reads these attributes when it builds its border windows, so
    this has to run before the PromptSession is constructed. Import-time is
    early enough, and the prompt is the only Frame in the process.
    """
    try:
        from prompt_toolkit.widgets.base import Border
    except ImportError:  # pragma: no cover - layout internals moved
        logger.debug("prompt_toolkit Border unavailable; input box keeps square corners")
        return
    Border.TOP_LEFT = "╭"
    Border.TOP_RIGHT = "╮"
    Border.BOTTOM_LEFT = "╰"
    Border.BOTTOM_RIGHT = "╯"


if PROMPT_TOOLKIT_AVAILABLE:
    _round_prompt_toolkit_corners()


# ------------------------------------------------------------ Color Palette
# Fixed hex colors per visual design spec — independent of terminal theme

COLORS = {
    "bg": "#1c1c1a",
    "primary": "#e8e6e3",
    "secondary": "#7a7873",
    "dim": "#4a4844",
    "accent": "#d97757",       # coral — prompt arrow, selection
    "tool": "#6cc8d4",         # cyan — tool names
    "success": "#7ec699",      # green — allowed, diff +
    "caution_border": "#6b5226",
    "caution_text": "#f0b429",
    "error_border": "#6b3232",
    "error_text": "#e8827c",
}


class CommandCompleter(Completer):
    """Custom completer for slash commands."""
    
    def __init__(self, command_registry):
        self.command_registry = command_registry
    
    def get_completions(self, document, complete_event):
        """Generate completions for slash commands."""
        text = document.text_before_cursor
        
        if not text.startswith('/'):
            return
        
        command_part = text[1:].lower()
        seen = set()
        
        for command in self.command_registry.commands():
            if command.name in seen:
                continue
            seen.add(command.name)
            
            if command.name.startswith(command_part):
                yield Completion(
                    text=command.name,
                    start_position=-len(command_part),
                    display=f"/{command.name}",
                    display_meta=command.summary,
                )


# ---------------------------------------------------------------------------
# Permission prompt helpers
# ---------------------------------------------------------------------------

# Verbs that describe what a tool is about to do — used in the warning header.
_TOOL_DESCRIPTIONS: dict[str, str] = {
    "write_file":    "This will write to a file",
    "create_file":   "This will create a new file",
    "modify_file":   "This will modify an existing file",
    "create_folder": "This will create a new folder",
    "execute_command": "This will run a shell command",
    "remember":      "This will store information in long-term memory",
    "delegate":      "This will spawn a sub-agent",
}


def _tool_description(tool_name: str, arguments: dict) -> str:
    """Return a one-line human description for the permission header.

    Falls back to a generic description when the tool isn't in the table.
    """
    if tool_name in _TOOL_DESCRIPTIONS:
        return _TOOL_DESCRIPTIONS[tool_name]
    # Plain text: this is appended to a rich Text, which does not parse markup.
    return f"This will run {tool_name}"


def _format_args(arguments: dict, max_value: int = 80) -> list[str]:
    """Format tool arguments as short display lines, truncating long values."""
    lines = []
    for key, value in arguments.items():
        v = str(value)
        if len(v) > max_value:
            v = v[:max_value - 1] + "…"
        lines.append(f"{key}: {v}")
    return lines


def _arrow_select(choices: list[tuple[str, bool, str]]) -> tuple[bool, str]:
    """Show an arrow-key selection menu using prompt_toolkit with coral accent.

    choices: List of (label, approved, scope).

    Returns (approved, scope) of the selected item.
    Falls back to _text_select on any error.
    """
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    selected = [0]  # mutable cell for the closure

    def get_text():
        # One flat list of (style, text) fragments. A list of HTML objects is
        # not valid formatted text — prompt_toolkit tries to unpack each item
        # as a (style, text) pair and raises.
        fragments = []
        for i, (label, _, _) in enumerate(choices):
            if i == selected[0]:
                fragments.append((f'fg:{COLORS["accent"]} bold', f'❯ {i + 1}  {label}\n'))
            else:
                fragments.append((f'fg:{COLORS["dim"]}', f'  {i + 1}  {label}\n'))
        return fragments

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        selected[0] = (selected[0] - 1) % len(choices)

    @kb.add("down")
    def _down(event):
        selected[0] = (selected[0] + 1) % len(choices)

    @kb.add("enter")
    def _enter(event):
        event.app.exit(result=selected[0])

    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(event):
        event.app.exit(result=None)

    layout = Layout(Window(FormattedTextControl(get_text, focusable=True)))
    app = Application(layout=layout, key_bindings=kb, full_screen=False)

    try:
        # The REPL is async, so a plain app.run() would try to start a second
        # event loop on this thread and raise — silently degrading every
        # permission prompt to the numbered fallback. in_thread=True gives the
        # menu its own loop, matching how ConsoleUI.prompt() drives its session.
        result = app.run(in_thread=True)
    except Exception as exc:
        logger.debug("arrow-key permission menu unavailable (%s); using text prompt", exc)
        return _text_select(choices)

    if result is None:
        # Ctrl+C / Ctrl+D → deny once
        return False, "once"
    return choices[result][1], choices[result][2]


def _text_select(choices: list[tuple[str, bool, str]]) -> tuple[bool, str]:
    """Numbered text fallback for non-terminal environments.

    choices: List of (label, approved, scope).

    Returns (approved, scope) of the selected item.
    """
    for i, (label, _, _) in enumerate(choices):
        print(f"  {i + 1}  {label}")
    while True:
        try:
            raw = input("❯ ").strip()
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(choices):
                    return choices[idx][1], choices[idx][2]
            print(f"Enter a number between 1 and {len(choices)}")
        except (KeyboardInterrupt, EOFError):
            return False, "once"


def _short_path(path: Path, max_len: int = 48) -> str:
    """Render a path compactly for the banner.

    Paths under the home directory collapse to ``~``. Anything still too long
    keeps its head and tail with an ellipsis between, since the tail is what
    identifies the file and the middle is usually a machine-generated temp
    directory nobody reads.
    """
    text = str(path)
    try:
        text = "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        pass

    if len(text) <= max_len:
        return text

    parts = Path(text).parts
    if len(parts) <= 3:
        return text
    return str(Path(parts[0]) / "…" / Path(*parts[-2:]))


_THOUGHT_BUFFER = 1000
_LOGO_PATH = Path(__file__).parent.parent.parent / "ascii-art.txt"

_LOGO_INK = {
    "#": ("█", "bold white"),
    "=": ("█", "#1971c2"),
    "+": ("█", "#2f9e44"),
}

_MCP_STATE_INK = {
    "connected": ("●", "green"),
    "failed": ("●", "red"),
    "disconnected": ("○", "yellow"),
}

_BAR_WIDTH = 24
_BAR_INK = ((80, "red"), (60, "yellow"), (0, "green"))


class ConsoleUI:
    """ConsoleUI renders one agent Session to the terminal.
    
    Simple Rich-based UI with clear colors and good readability.
    """

    def __init__(
        self,
        console: Console | None = None,
        streams: bool = True,
        show_thinking: bool = True,
        command_registry = None,
        log_path: Path | None = None,
        log_notice: str | None = None,
    ) -> None:
        """Initialize a ConsoleUI bound to a rich Console.

        log_path:   JSON-lines log file the session writes to, shown in the
                    banner so it can be found without grepping the config.
        log_notice: "what happened|what to do" for a degraded log destination,
                    boxed under the banner. None when logging went as asked.
        """
        self.console = console or Console()
        self.streams = streams
        self.show_thinking = show_thinking
        self.command_registry = command_registry
        self.log_path = log_path
        self.log_notice = log_notice
        # Problems that happened before the first prompt, boxed under the banner.
        # The log destination is the first of them; MCP and anything else that
        # fails during start-up appends its own through notice().
        self._notices: list[str] = [log_notice] if log_notice else []
        self._banner_printed = False
        self._status: Status | None = None
        self._thinking_since: float | None = None
        self._thought = ""
        self._answer_started = False

        # Live sub-agent cards. Register it on the hook registry to feed it:
        #   get_hook_registry().register(ui.subagents)
        # Rich allows one live region per console and the thinking spinner is
        # one, so the view stops the spinner before taking the region over.
        self.subagents = SubAgentLiveView(self.console, COLORS, on_takeover=self._stop_status)

        # Initialize prompt_toolkit session if available
        self._prompt_session = None
        if PROMPT_TOOLKIT_AVAILABLE and self.console.is_terminal:
            prompt_style = Style.from_dict({
                'prompt': f'{COLORS["accent"]} bold',
                'placeholder': f'{COLORS["secondary"]} italic',
                '': f'bold {COLORS["primary"]}',
                'completion-menu': f'bg:{COLORS["bg"]} {COLORS["primary"]}',
                'completion-menu.completion': f'bg:{COLORS["bg"]} {COLORS["primary"]}',
                'completion-menu.completion.current': f'bg:{COLORS["accent"]} {COLORS["primary"]} bold',
                'completion-menu.meta': f'bg:{COLORS["bg"]} {COLORS["secondary"]}',
                'completion-menu.meta.current': f'bg:{COLORS["accent"]} {COLORS["primary"]}',
                'scrollbar.background': f'bg:{COLORS["bg"]}',
                'scrollbar.button': f'bg:{COLORS["secondary"]}',
                # Neutral border around the active input line — the box is the
                # spec's third boxed moment, alongside permission and error.
                'frame.border': COLORS["dim"],
            })
            
            kb = KeyBindings()
            
            @kb.add('s-tab')
            def _(event):
                buffer = event.app.current_buffer
                if not buffer.text.startswith('/'):
                    buffer.insert_text('/')
            
            completer = None
            if command_registry:
                completer = CommandCompleter(command_registry)
            
            self._prompt_session = PromptSession(
                style=prompt_style,
                key_bindings=kb,
                completer=completer,
                complete_while_typing=True,
                complete_in_thread=True,
                mouse_support=False,  # Disable to allow terminal scrolling with mouse/trackpad
                wrap_lines=True,
                show_frame=True,
                # The frame is erased on submit and re-echoed as one line by
                # _echo_submitted; keeping it would cost 3 scrollback lines a turn.
                erase_when_done=True,
                rprompt=lambda: HTML(f'<style fg="{COLORS["secondary"]}">Tab completes · ↑ history · Shift+Tab for /</style>'),
            )

    # ------------------------------------------------------------------ chrome

    def banner(self, mode: str, model: str) -> None:
        """Print the session header.

        Deliberately unboxed. Boxes are reserved for the three moments that
        need the person — active input, a permission prompt, a turn-ending
        error — and a header that is always there is none of them. Boxing it
        is what stops a real error box from standing out.
        """
        self._print_ascii_logo()
        self.console.print()
        self.console.print(
            Text("  AI Engineering Lab · University of Passau (SoSe 2026)", style=COLORS["dim"])
        )
        self.console.print()

        rows: list[tuple[str, Text]] = [
            ("model", Text(model, style=COLORS["primary"])),
            ("mode", Text(mode, style=COLORS["primary"])),
        ]
        if self.log_path is not None:
            logs = Text(_short_path(self.log_path), style=COLORS["primary"])
            logs.append("  jq-readable", style=COLORS["dim"])
            rows.append(("logs", logs))

        label_width = max(len(label) for label, _ in rows)
        for label, value in rows:
            line = Text(f"  {label.ljust(label_width)}  ", style=COLORS["secondary"])
            line.append_text(value)
            self.console.print(line)

        self.console.print()
        hint = Text("  ", style=COLORS["secondary"])
        hint.append("/help", style=COLORS["accent"])
        hint.append(" commands · ", style=COLORS["secondary"])
        hint.append("Tab", style=COLORS["accent"])
        hint.append(" complete · ", style=COLORS["secondary"])
        hint.append("Ctrl+C", style=COLORS["accent"])
        hint.append(" exit", style=COLORS["secondary"])
        self.console.print(hint)
        self.console.print()

        # Start-up problems are worth a box: they happened before the session
        # started and the person may want to act on them.
        self._banner_printed = True
        for text in self._notices:
            self._render_error(text)
            self.console.print()


    def _print_ascii_logo(self) -> None:
        """Print the nikola lockup."""
        try:
            lines = _LOGO_PATH.read_text(encoding="utf-8").rstrip("\n").split("\n")
        except OSError as exc:
            logger.debug("ASCII logo unreadable at %s (%s); using wordmark", _LOGO_PATH, exc)
            self._print_simple_logo()
            return

        art_width = max((len(line) for line in lines), default=0)
        if art_width > self.console.width:
            logger.debug("ASCII logo needs %d columns, console has %d; using wordmark", art_width, self.console.width)
            self._print_simple_logo()
            return

        for line in lines:
            rendered = Text()
            for char in line:
                glyph, style = _LOGO_INK.get(char, (char, "bold white"))
                rendered.append(glyph, style=style)
            self.console.print(rendered)

    def _print_simple_logo(self) -> None:
        """Print the wordmark on one line."""
        self.console.print(Text("nikola", style="bold white"))

    def prompt(self) -> str:
        """Read one line of user input.

        The box belongs to the *active* input only. On submit it is erased and
        replaced by a single `❯ text` line, so one turn costs one line of
        scrollback rather than three.
        """
        if self._prompt_session is not None:
            try:
                self._stop_status()
                with patch_stdout():
                    result = self._prompt_session.prompt(
                        # Leading space pads the arrow off the box's left border.
                        HTML(' <prompt>❯</prompt> '),
                        placeholder=HTML('<placeholder>Build Anything · @ for context · / for commands · $ for shell</placeholder>'),
                        enable_history_search=True,
                        in_thread=True,
                    )
                text = result.strip()
                self._echo_submitted(text)
                return text
            except Exception as e:
                logger.debug(f"prompt_toolkit failed, falling back to input(): {e}")

        return input(self._prompt_text()).strip()

    def _echo_submitted(self, text: str) -> None:
        """Leave the submitted line in the transcript after the box is erased.

        Nothing is echoed for an empty submission — the REPL just prompts
        again, and a bare arrow would be noise.
        """
        if not text:
            return
        line = Text()
        line.append("❯ ", style=f"bold {COLORS['accent']}")
        line.append(text, style=COLORS["primary"])
        self.console.print(line)

    def _prompt_text(self) -> str:
        """Build the prompt for readline with coral accent arrow."""
        from agent.cli.completion import is_available, is_libedit

        if not self.console.is_terminal:
            return "You > "
        if not is_available() or is_libedit():
            return "❯ "
        # Use coral accent color for the arrow
        return "\001\x1b[38;2;217;119;87m\002❯\001\x1b[0m\002 "

    def goodbye(self, message: str = "Goodbye!") -> None:
        """Close the session."""
        self._stop_status()
        self.console.print(Text(message, style=COLORS["secondary"]))

    def notice(self, what: str, todo: str = "") -> None:
        """Report a start-up problem in the box under the banner.

        Degraded start-up — a log file that could not be opened, an MCP server
        that never came up — is the one thing a person cannot see from the
        transcript, because the session carries on as if nothing happened. It
        gets the same red box as a turn-ending error.

        Called before banner(), the notice is held and drawn with it, below the
        logo where it is legible. Called after, it prints immediately, so the
        caller never has to know which side of the banner it is on.

        what: what went wrong, one line.
        todo: what the person can do about it; omitted when there is nothing.
        """
        text = f"{what}|{todo}" if todo else what
        if self._banner_printed:
            self._render_error(text)
            self.console.print()
            return
        self._notices.append(text)

    def error(self, message: str) -> None:
        """Report a failure (non-boxed, for routine errors)."""
        self._stop_status()
        self.console.print(Text(f"✕ {message}", style=f"bold {COLORS['error_text']}"))

    def info(self, message: str) -> None:
        """Report information."""
        self._stop_status()
        self.console.print(Text(f"ℹ {message}", style=COLORS["tool"]))

    def hint(self, message: str) -> None:
        """Print a hint."""
        self._stop_status()
        self.console.print(Text(f"💡 {message}", style=COLORS["secondary"]))

    # (label, approved, scope) — order determines menu position
    _PERMISSION_CHOICES: ClassVar[list[tuple[str, bool, str]]] = [
        ("Yes",                               True,  "once"),
        ("Yes, don't ask again for this tool", True,  "session"),
        ("No",                                False, "once"),
        ("No, never ask for this tool",        False, "session"),
    ]

    def confirm_tool_use(self, tool_name: str, arguments: dict) -> tuple[bool, str]:
        """Prompt the user to allow or deny a tool call with amber-bordered box.

        Renders an arrow-key selection menu when a terminal is available,
        otherwise falls back to a numbered text prompt.

        Args:
            tool_name:  Name of the tool requesting execution.
            arguments:  Tool arguments shown for context.

        Returns:
            Tuple of (approved: bool, scope: str).
            scope is "once" or "session".
        """
        # The menu reads keys from the terminal, which a live region would be
        # repainting over. Sub-agents auto-deny rather than ask, so this only
        # closes cards left open by a crashed delegation.
        self.subagents.close()
        self._stop_status()

        description = _tool_description(tool_name, arguments)
        arg_lines = _format_args(arguments)

        # Build panel content
        content = Text()
        content.append("⚠ Permission needed", style=f"bold {COLORS['caution_text']}")
        content.append(" — ", style=COLORS['caution_text'])
        content.append(f"{description}\n\n", style=COLORS['primary'])
        
        # Add arguments
        for line in arg_lines:
            content.append(f"  {line}\n", style=COLORS['secondary'])
        
        if arg_lines:
            content.append("\n", style="")
        
        content.append("Allow ", style=COLORS['primary'])
        content.append(tool_name, style=COLORS['tool'])
        content.append(" to run?", style=COLORS['primary'])
        
        # Display permission panel
        panel = Panel(
            content,
            border_style=COLORS['caution_border'],
            padding=(1, 2),
            expand=False,
        )
        self.console.print()
        self.console.print(panel)
        self.console.print()

        if PROMPT_TOOLKIT_AVAILABLE and self.console.is_terminal:
            return _arrow_select(self._PERMISSION_CHOICES)
        return _text_select(self._PERMISSION_CHOICES)


    def table(self, title: str, rows: Iterable[tuple[str, str]]) -> None:
        """Print a two-column listing."""
        self._stop_status()
        table = Table(box=box.SIMPLE, show_header=False, title=title, title_justify="left")
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="dim")
        for name, description in rows:
            table.add_row(name, description)
        self.console.print(table)

    def usage_bar(self, label: str, pct: int, caption: str = "") -> None:
        """Print a labelled meter."""
        self._stop_status()
        filled = max(0, min(_BAR_WIDTH, round(pct / 100 * _BAR_WIDTH)))
        style = next(style for threshold, style in _BAR_INK if pct >= threshold)

        bar = Text(f"{label}  ", style="bold cyan")
        bar.append("█" * filled, style=style)
        bar.append("░" * (_BAR_WIDTH - filled), style="dim")
        bar.append(f"  {pct}%", style=style)
        if caption:
            bar.append(f"  {caption}", style="dim")
        self.console.print(bar)

    def mcp_overview(
        self,
        servers: Iterable[tuple[str, str, Iterable[tuple[str, str]], str | None]],
    ) -> None:
        """Print MCP servers with tools."""
        self._stop_status()
        table = Table(box=box.SIMPLE, show_header=False, title="MCP Servers", title_justify="left")
        table.add_column(no_wrap=True)
        table.add_column(style="dim")

        for name, state, tools, error in servers:
            glyph, style = _MCP_STATE_INK.get(state, _MCP_STATE_INK["disconnected"])
            tool_list = list(tools)
            plural = "" if len(tool_list) == 1 else "s"
            table.add_row(
                Text.assemble((f"{glyph} ", style), (name, "bold cyan")),
                Text(f"{state} · {len(tool_list)} tool{plural}", style=style),
            )
            if error:
                table.add_row(Text(f"    ✗ {error}", style="red dim"), "")
            for tool_name, summary in tool_list:
                table.add_row(Text(f"    {tool_name}", style="cyan"), summary)

        self.console.print(table)
    
    def print_code(self, code: str, filename: str | None = None, language: str = "python") -> None:
        """Print code with syntax highlighting."""
        self._stop_status()
        syntax = Syntax(code, language, theme="monokai", line_numbers=False)
        if filename:
            self.console.print(Panel(syntax, title=f"[cyan]{filename}[/]", border_style="cyan"))
        else:
            self.console.print(syntax)

    # ------------------------------------------------------------ turn lifecycle

    def begin_turn(self) -> None:
        """Announce that a turn is in flight."""
        self._answer_started = False
        self._start_status("waiting for the model…")

    def finish_turn(self) -> None:
        """Close any spinner and separate this turn from the next."""
        # A sub-agent that died without its end event would otherwise hold the
        # live region past the turn, freezing the next prompt behind it.
        self.subagents.close()
        self._end_thinking()
        self._stop_status()
        self.console.print()

    def print_answer(self, answer: str) -> None:
        """Print a complete answer."""
        self._stop_status()
        self.console.print(Text("Agent", style=f"bold {COLORS['tool']}"))
        self.console.print(answer, markup=False, highlight=False, soft_wrap=True)

    # ------------------------------------------------------------------- sink

    def sink(self, kind: str, text: str) -> None:
        """Render one fragment from the loop.
        
        Supports kinds: reasoning, tool, answer, error, permission.
        """
        if kind == "reasoning":
            self._render_thinking(text)
            return

        self._end_thinking()
        
        if kind == "tool":
            self._render_tool(text)
            return
        
        if kind == "error":
            self._render_error(text)
            return
        
        if kind == "permission":
            # Permission rendering is handled by confirm_tool_use
            # This branch exists for consistency but shouldn't be called directly
            return
        
        self._render_answer(text)

    # --------------------------------------------------------------- internals

    def _render_thinking(self, text: str) -> None:
        """Fold a thinking fragment into the spinner caption."""
        if self._thinking_since is None:
            self._thinking_since = time.monotonic()

        self._thought = (self._thought + text)[-_THOUGHT_BUFFER:]
        self._start_status(self._caption())

    def _caption(self) -> str:
        """Build the spinner caption."""
        if not self.show_thinking:
            return "thinking…"
        
        cleaned = ' '.join(self._thought.split())
        if not cleaned:
            return "thinking…"
        
        width = self.console.width - 10
        if len(cleaned) <= width:
            return cleaned
        
        return "…" + cleaned[-(width - 1):]

    def _end_thinking(self) -> None:
        """Close a thinking phase."""
        if self._thinking_since is None:
            return
        elapsed = time.monotonic() - self._thinking_since
        self._thinking_since = None
        self._thought = ""
        self._stop_status()
        self.console.print(Text(f"✻ Thought for {elapsed:.1f}s", style=COLORS['secondary']))

    # Tools whose call line is replaced by a live card rather than printed.
    # Their observation line still prints: it is what the parent actually got
    # back, which the card does not show.
    _CARDED_TOOLS = ("delegate(", "delegate_parallel(")

    def _render_tool(self, line: str) -> None:
        """Print one tool trace line with color-coded formatting."""
        stripped = line.rstrip("\n").strip()
        if stripped.startswith(self._CARDED_TOOLS):
            return
        if stripped.startswith(("→", "⎿")):
            # Tool result / observation — dim text, green for success markers.
            # Test the body, not the glyph-prefixed line, so diff '+' lines match.
            body = stripped[1:].lstrip()
            if "✓" in body or body.startswith("+"):
                self.console.print(Text(f"  {stripped}", style=f"bold {COLORS['success']}"))
            else:
                self.console.print(Text(f"  {stripped}", style=COLORS['secondary']))
        else:
            # Tool call — cyan tool name, dim args
            # Format: tool_name(args) or just tool_name
            if "(" in stripped:
                name_part = stripped.split("(", 1)[0]
                rest = "(" + stripped.split("(", 1)[1]
                text = Text()
                text.append("⏺ ", style=COLORS['tool'])
                text.append(name_part, style=COLORS['tool'])
                text.append(rest, style=COLORS['secondary'])
                self.console.print(text)
            else:
                self.console.print(Text(f"⏺ {stripped}", style=COLORS['tool']))

    def _render_answer(self, text: str) -> None:
        """Stream answer text."""
        self._stop_status()
        if not self._answer_started:
            self._answer_started = True
            self.console.print(Text("Agent", style=f"bold {COLORS['tool']}"))
        self.console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    def _start_status(self, caption: str) -> None:
        """Start or re-caption the spinner."""
        if not self.console.is_terminal:
            return
        # The sub-agent cards hold the console's one live region while they run,
        # and a second Live would raise. Their spinners already say work is
        # happening, so there is nothing to add.
        if self.subagents.is_live:
            return
        if self._status is None:
            self._status = self.console.status(Text(caption, style="dim"), spinner="dots")
            self._status.start()
        else:
            self._status.update(Text(caption, style="dim"))

    def _stop_status(self) -> None:
        """Stop the spinner."""
        if self._status is not None:
            self._status.stop()
            self._status = None
    
    def _render_error(self, text: str) -> None:
        """Render a turn-ending error in a red-bordered box.
        
        Args:
            text: Error message, expected format "what happened|what to do" or single message
        """
        self._stop_status()
        
        # Parse two-part error messages
        parts = text.split("|", 1) if "|" in text else [text, ""]
        what_happened = parts[0].strip()
        what_to_do = parts[1].strip() if len(parts) > 1 else ""

        # The header already says "Error" — a second prefix inside the box is noise.
        if what_happened.lower().startswith("error:"):
            what_happened = what_happened[len("error:"):].lstrip()

        # Build panel content
        content = Text()
        content.append("✕ Error", style=f"bold {COLORS['error_text']}")
        if what_happened:
            content.append(" — ", style=COLORS['error_text'])
            content.append(what_happened, style=COLORS['primary'])

        if what_to_do:
            content.append("\n\n", style="")
            content.append(what_to_do, style=COLORS['secondary'])
        
        panel = Panel(
            content,
            border_style=COLORS['error_border'],
            padding=(1, 2),
            expand=False,
        )
        self.console.print(panel)
