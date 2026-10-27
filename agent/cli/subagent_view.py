"""Live sub-agent cards for the terminal.

While the parent delegates, it is blocked inside one tool call and the
transcript has nothing to say. This module fills that gap with one card per
running sub-agent, refreshed in place:

    ╭────────────────────────────────────────────────────────────────╮
    │ You are reviewing a code diff for STANDARDS compliance. Your…  ›│
    │ ⠙ Running · file-scout · 5 tools · 22.4k · 6s                   │
    ╰────────────────────────────────────────────────────────────────╯

The card's top line is the task the sub-agent was given, truncated to one line;
the meta line carries a spinner while it works and its totals as they grow.
Parallel sub-agents stack, so a delegate_parallel batch reads as a batch.

This is the only place sub-agent state becomes pixels. The counters arrive as
hook events (agent.hooks.SubAgentStartEvent / SubAgentProgressEvent /
SubAgentEndEvent), so the orchestrator stays free of presentation and this view
is registered like any other observability hook:

    registry.register(ui.subagents)

Rich allows one live region per console, and ConsoleUI's thinking spinner is
one. The view therefore calls ``on_takeover`` before it starts, and ConsoleUI
refuses to restart its spinner while ``is_live`` — see agent.cli.ui.

See also: agent.subagents.progress, agent.subagents.orchestrator.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

RUNNING = "running"
DONE = "done"
FAILED = "failed"

# Fast enough for the spinner to read as motion, slow enough to stay cheap when
# five sub-agents are running at once.
_REFRESH_PER_SECOND = 12


def format_tokens(count: int) -> str:
    """Render a token count the way the meta line wants it.

    Example:
        >>> format_tokens(22418)
        '22.4k'
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def format_elapsed(seconds: float) -> str:
    """Render a duration compactly.

    Example:
        >>> format_elapsed(6.4)
        '6s'
        >>> format_elapsed(75)
        '1m 15s'
    """
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{int(seconds)}s"


@dataclass
class SubAgentCard:
    """One sub-agent's state, as the card shows it."""

    subagent_id: str
    role: str
    task: str
    started: float
    state: str = RUNNING
    tools: int = 0
    tokens: int = 0
    finished: float | None = None

    @property
    def elapsed(self) -> float:
        """Seconds since the sub-agent started, frozen once it finishes."""
        return (self.finished if self.finished is not None else time.monotonic()) - self.started


class SubAgentLiveView:
    """Renders running sub-agents as live cards, and nothing when there are none.

    Implements the observability hook methods, so registering it on the global
    hook registry is all the wiring it needs.

    Off a terminal — piped output, a captured test console — there is no live
    region to update, so each sub-agent gets one line when it starts and one
    when it ends instead.
    """

    def __init__(
        self,
        console: Console,
        colors: dict[str, str],
        on_takeover: Callable[[], None] | None = None,
    ) -> None:
        """Bind the view to a console.

        console:     the console ConsoleUI writes to — the same one, or the
                     live region and the transcript would fight.
        colors:      the palette dict from agent.cli.ui.
        on_takeover: called before the live region starts, so the owner can
                     stop whatever else was animating.
        """
        self.console = console
        self.colors = colors
        self.on_takeover = on_takeover
        self._cards: dict[str, SubAgentCard] = {}
        self._order: list[str] = []
        # Held by the Live refresh thread while rendering and by the event loop
        # while updating, so the two never read a half-written card.
        self._lock = threading.RLock()
        self._live: Live | None = None

    @property
    def is_live(self) -> bool:
        """True while the view owns the console's live region."""
        return self._live is not None

    # ---------------------------------------------------------------- hooks

    def on_subagent_start(self, event) -> None:
        """Open a card for a sub-agent that just started."""
        self.start(event.subagent_id, event.role_name, event.task)

    def on_subagent_progress(self, event) -> None:
        """Fold new totals into a card."""
        self.update(
            event.subagent_id,
            tools=event.tool_calls,
            tokens=event.total_tokens,
        )

    def on_subagent_end(self, event) -> None:
        """Close a card, and the live region with it if nothing is left."""
        self.finish(event.subagent_id, success=event.success)

    # --------------------------------------------------------------- state

    def start(self, subagent_id: str, role: str, task: str) -> None:
        """Add a card and start the live region if it isn't running."""
        with self._lock:
            if subagent_id in self._cards:
                return
            card = SubAgentCard(
                subagent_id=subagent_id,
                role=role,
                task=task,
                started=time.monotonic(),
            )
            self._cards[subagent_id] = card
            self._order.append(subagent_id)

            if not self.console.is_terminal:
                to_print, to_start = card, None
            elif self._live is None:
                self._live = Live(
                    get_renderable=self._renderable,
                    console=self.console,
                    refresh_per_second=_REFRESH_PER_SECOND,
                    transient=False,
                )
                to_print, to_start = None, self._live
            else:
                to_print, to_start = None, None

        # Everything that reaches into rich happens outside our lock. The
        # refresh thread holds rich's lock while it calls _renderable, which
        # wants ours — taking them in the other order here would deadlock.
        if to_print is not None:
            self._print_plain_start(to_print)
        if to_start is not None:
            if self.on_takeover is not None:
                self.on_takeover()
            to_start.start()

    def update(
        self,
        subagent_id: str,
        tools: int | None = None,
        tokens: int | None = None,
    ) -> None:
        """Update one card's counters. Unknown ids are ignored."""
        with self._lock:
            card = self._cards.get(subagent_id)
            if card is None:
                return
            if tools is not None:
                card.tools = tools
            if tokens is not None:
                card.tokens = tokens

    def finish(self, subagent_id: str, success: bool) -> None:
        """Mark a card done and, once none are running, close the region.

        The finished cards are left in the scrollback rather than erased: they
        are the record of what the delegation cost.
        """
        with self._lock:
            card = self._cards.get(subagent_id)
            if card is None:
                return
            card.state = DONE if success else FAILED
            card.finished = time.monotonic()
            all_finished = not any(c.state == RUNNING for c in self._cards.values())

        # Outside the lock, for the reason given in start().
        if not self.console.is_terminal:
            self._print_plain_finish(card)
        if all_finished:
            self.close()

    def close(self) -> None:
        """Stop the live region and forget the cards.

        Safe to call when nothing is live — ConsoleUI calls it at every turn
        boundary so a sub-agent that died mid-run cannot strand the region.
        """
        with self._lock:
            live, self._live = self._live, None

        if live is not None:
            # Paint while the cards still exist: stopping runs one last render,
            # and an already-emptied view would erase them off the screen
            # instead of leaving the finished set in the scrollback.
            try:
                live.refresh()
            except Exception:
                pass
            live.stop()

        with self._lock:
            self._cards.clear()
            self._order.clear()

    # ------------------------------------------------------------ rendering

    def _renderable(self):
        """Build the current frame. Called by the Live refresh thread."""
        with self._lock:
            cards = [self._cards[key] for key in self._order if key in self._cards]
        return Group(*[self._card_panel(card) for card in cards])

    def _card_panel(self, card: SubAgentCard) -> Panel:
        """Render one card: task on top, status beneath, chevron at the right."""
        task = Text(" ".join(card.task.split()), style=self.colors["primary"], no_wrap=True, overflow="ellipsis")

        body = Table.grid(expand=True)
        body.add_column(ratio=1, overflow="ellipsis")
        body.add_column(width=1, justify="right", vertical="middle")
        body.add_row(Group(task, self._meta(card)), Text("›", style=self.colors["dim"]))

        return Panel(
            body,
            box=box.ROUNDED,
            border_style=self.colors["error_border"] if card.state == FAILED else self.colors["dim"],
            padding=(0, 1),
        )

    def _meta(self, card: SubAgentCard):
        """Build the status line — a spinner while running, a glyph once done.

        Running and finished cards share one shape, so a card does not jump
        when it lands: status, then role, then what it spent.
        """
        chips = f"{card.role} · {self._counters(card)}"

        if card.state == RUNNING:
            line = Text("", no_wrap=True, overflow="ellipsis")
            line.append("Running", style=self.colors["secondary"])
            line.append(f" · {chips}", style=self.colors["dim"])
            # The spinner supplies its own leading glyph and the space after it.
            return Spinner("dots", text=line, style=self.colors["tool"])

        glyph, label, style = (
            ("✓", "Done", self.colors["success"])
            if card.state == DONE
            else ("✕", "Failed", self.colors["error_text"])
        )
        line = Text("", no_wrap=True, overflow="ellipsis")
        line.append(f"{glyph} ", style=style)
        line.append(label, style=self.colors["secondary"])
        line.append(f" · {chips}", style=self.colors["dim"])
        return line

    @staticmethod
    def _counters(card: SubAgentCard) -> str:
        """The spend chips: tool calls, tokens, wall time.

        A count appears once there is one to report — a fresh card saying
        "0 tools · 0" reads as a stalled sub-agent rather than a new one.
        """
        chips = []
        if card.tools:
            chips.append(f"{card.tools} tool{'' if card.tools == 1 else 's'}")
        if card.tokens:
            chips.append(format_tokens(card.tokens))
        chips.append(format_elapsed(card.elapsed))
        return " · ".join(chips)

    # ------------------------------------------------------- plain fallback

    def _print_plain_start(self, card: SubAgentCard) -> None:
        """One line for a sub-agent start when there is no live region."""
        line = Text("⏺ ", style=self.colors["tool"])
        line.append(card.role, style=self.colors["tool"])
        line.append(f" {' '.join(card.task.split())}", style=self.colors["secondary"])
        self.console.print(line, no_wrap=True, overflow="ellipsis")

    def _print_plain_finish(self, card: SubAgentCard) -> None:
        """One line for a sub-agent end when there is no live region."""
        ok = card.state == DONE
        line = Text(f"  ⎿ {'✓' if ok else '✕'} ", style=self.colors["success"] if ok else self.colors["error_text"])
        line.append(f"{card.role} · {self._counters(card)}", style=self.colors["dim"])
        self.console.print(line)
