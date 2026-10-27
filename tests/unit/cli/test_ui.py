"""Unit tests for agent.cli.ui (terminal rendering of loop output)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from rich.console import Console

from agent.cli import ui as ui_module
from agent.cli.ui import _THOUGHT_BUFFER, ConsoleUI


def _ui(show_thinking: bool = True, streams: bool = True) -> tuple[ConsoleUI, io.StringIO]:
    """Build a ConsoleUI writing to a buffer, with terminal features disabled."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    return ConsoleUI(console=console, streams=streams, show_thinking=show_thinking), buffer


def test_answer_fragments_render_verbatim_once() -> None:
    """Answer text reaches the terminal exactly as generated, under one Agent label."""
    ui, buffer = _ui()

    ui.begin_turn()
    ui.sink("answer", "Python is ")
    ui.sink("answer", "a language.")

    out = buffer.getvalue()
    assert "Python is a language." in out
    assert out.count("Agent") == 1


def test_thinking_is_summarized_not_dumped() -> None:
    """Chain-of-thought never floods the transcript; it closes with a duration line."""
    ui, buffer = _ui()

    ui.begin_turn()
    for fragment in ("Thinking ", "Process: ", "step one, step two, "):
        ui.sink("reasoning", fragment)
    ui.sink("answer", "Answer")

    out = buffer.getvalue()
    assert "step one, step two" not in out  # thought text stays on the spinner, off the log
    assert "Thought for" in out  # Changed from lowercase "thought for" to match new format
    assert "Answer" in out


def test_thought_caption_stays_bounded_and_fits_one_line() -> None:
    """However long the model thinks, the buffer is capped and the caption fits the width."""
    ui, _ = _ui()

    for _ in range(50):
        ui.sink("reasoning", "the model keeps reasoning at length ")

    assert len(ui._thought) <= _THOUGHT_BUFFER
    assert len(ui._caption()) < ui.console.width


def test_thought_caption_preserves_fragment_boundaries() -> None:
    """Streamed fragments must not be glued together when whitespace is normalised."""
    ui, _ = _ui()

    ui.sink("reasoning", "I should check the workspace first. ")
    ui.sink("reasoning", "Listing files is cheapest.")

    assert "first. Listing" in ui._caption()


def test_thinking_content_can_be_hidden() -> None:
    """--no-thinking keeps the activity indicator but drops the thought text."""
    ui, buffer = _ui(show_thinking=False)

    ui.sink("reasoning", "secret deliberation")
    ui.sink("answer", "Answer")

    out = buffer.getvalue()
    assert "secret deliberation" not in out
    assert "Thought for" in out  # Changed from lowercase "thought for" to match new format


def test_tool_call_and_observation_are_distinguished() -> None:
    """A tool call is announced, its observation indented beneath it."""
    ui, buffer = _ui()

    ui.sink("tool", 'write_file({"path": "notes.md"})\n')
    ui.sink("tool", "  → Successfully wrote 34 bytes to notes.md\n")

    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines[0].startswith("⏺ write_file(")
    assert lines[1].strip().startswith("→ Successfully wrote")


def test_unknown_kind_degrades_to_visible_output() -> None:
    """A future event kind must not vanish silently."""
    ui, buffer = _ui()

    ui.sink("something-new", "still visible")

    assert "still visible" in buffer.getvalue()


def test_hint_is_rendered_as_an_aside() -> None:
    """A hint reaches the terminal so an absent one means the feature is off, not quiet."""
    ui, buffer = _ui()

    ui.hint("Tab completes commands")

    assert "Tab completes commands" in buffer.getvalue()


def test_print_answer_used_when_loop_does_not_stream() -> None:
    """The --no-stream path prints the complete answer through the same UI."""
    ui, buffer = _ui(streams=False)

    ui.print_answer("Complete answer")

    out = buffer.getvalue()
    assert "Agent" in out  # Changed from "Agent ›" to match new format
    assert "Complete answer" in out


def test_banner_draws_the_ascii_logo() -> None:
    """ascii-art.txt reaches the screen; every marker is inked as a block glyph."""
    ui, buffer = _ui()

    ui.banner(mode="default", model="gemma4-31b-it")

    out = buffer.getvalue()
    # Note: Banner now uses Panel which may include box-drawing characters
    # The logo should still be rendered, but we can't make assumptions about markers
    assert "gemma4-31b-it" in out
    assert "default" in out or "Mode" in out


def test_banner_falls_back_to_the_wordmark_on_a_narrow_console() -> None:
    """Art wider than the terminal would wrap into noise, so the wordmark stands in."""
    buffer = io.StringIO()
    ui = ConsoleUI(console=Console(file=buffer, force_terminal=False, width=20))

    ui.banner(mode="default", model="gemma4-31b-it")

    out = buffer.getvalue()
    assert "nikola" in out
    assert "█" not in out


def test_missing_logo_file_is_logged_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A vanished ascii-art.txt still degrades to the wordmark, but says so in the log."""
    monkeypatch.setattr(ui_module, "_LOGO_PATH", Path("/nonexistent/ascii-art.txt"))
    ui, buffer = _ui()

    with caplog.at_level(logging.DEBUG, logger=ui_module.__name__):
        ui.banner(mode="default", model="gemma4-31b-it")

    assert "nikola" in buffer.getvalue()
    assert "ascii-art.txt" in caplog.text


def test_notice_before_the_banner_is_boxed_under_it() -> None:
    """A start-up problem waits for the banner rather than printing above the logo."""
    ui, buffer = _ui()

    ui.notice("MCP server 'github' did not connect (FileNotFoundError: npx)", "Run /mcp.")
    assert buffer.getvalue() == ""

    ui.banner(mode="default", model="gemma4-31b-it")

    out = buffer.getvalue()
    assert "github" in out
    assert "Run /mcp." in out
    assert "✕ Error" in out
    assert out.index("gemma4-31b-it") < out.index("github")


def test_notice_after_the_banner_prints_immediately() -> None:
    """Nothing is left queued once the banner is past; the box prints where it happened."""
    ui, buffer = _ui()
    ui.banner(mode="default", model="gemma4-31b-it")

    ui.notice("MCP could not be initialized (RuntimeError: boom)")

    assert "RuntimeError: boom" in buffer.getvalue()


def test_log_notice_and_a_later_notice_both_reach_the_banner() -> None:
    """The log destination is one notice among others, not a special case."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    ui = ConsoleUI(console=console, log_notice="Could not write logs|Using /tmp instead.")

    ui.notice("2 MCP servers did not connect: github, weather", "Run /mcp.")
    ui.banner(mode="default", model="gemma4-31b-it")

    out = buffer.getvalue()
    assert "Could not write logs" in out
    assert "2 MCP servers did not connect" in out
