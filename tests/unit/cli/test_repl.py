"""Unit tests for agent.cli.repl."""

from __future__ import annotations

import io

import pytest
from openai.types.chat import ChatCompletionMessageParam
from rich.console import Console

from agent.cli.repl import load_system_prompt, run_repl
from agent.cli.ui import ConsoleUI


def test_load_system_prompt_default() -> None:
    """Default system prompt loads content from agent/prompts/default.md."""
    prompt = load_system_prompt("default")
    assert "You are Nikola" in prompt
    assert "cannot delete files or folders" in prompt


def test_load_system_prompt_missing_raises_file_not_found() -> None:
    """Non-existent mode raises FileNotFoundError at startup."""
    with pytest.raises(FileNotFoundError):
        load_system_prompt("non_existent_mode_xyz")


@pytest.mark.asyncio
async def test_run_repl_one_shot(capsys: pytest.CaptureFixture[str]) -> None:
    """One-shot mode executes one turn, prints answer, and returns."""
    executed_messages: list[list[ChatCompletionMessageParam]] = []

    async def fake_process_turn(messages: list[ChatCompletionMessageParam]) -> str:
        executed_messages.append(messages)
        return "One shot executed successfully"

    await run_repl(
        process_turn_fn=fake_process_turn,  # type: ignore[arg-type]
        one_shot_prompt="Hello one shot",
        mode="default",
    )

    captured = capsys.readouterr()
    assert "One shot executed successfully" in captured.out
    assert len(executed_messages) == 1
    # System prompt + user prompt
    assert executed_messages[0][0]["role"] == "system"
    assert executed_messages[0][1]["role"] == "user"
    assert executed_messages[0][1]["content"] == "Hello one shot"


@pytest.mark.asyncio
async def test_run_repl_one_shot_streaming_ui_does_not_reprint() -> None:
    """When the loop renders answers through the UI, run_repl must not echo them."""
    buffer = io.StringIO()
    ui = ConsoleUI(console=Console(file=buffer, force_terminal=False, width=100), streams=True)

    async def fake_process_turn(messages: list[ChatCompletionMessageParam]) -> str:
        ui.sink("answer", "Streamed answer")
        return "Streamed answer"

    await run_repl(
        process_turn_fn=fake_process_turn,  # type: ignore[arg-type]
        one_shot_prompt="Hello one shot",
        mode="default",
        ui=ui,
    )

    assert buffer.getvalue().count("Streamed answer") == 1


@pytest.mark.asyncio
async def test_run_repl_one_shot_shows_the_banner_at_a_terminal() -> None:
    """A person running --prompt sees the branding the interactive session would give them."""
    buffer = io.StringIO()
    ui = ConsoleUI(console=Console(file=buffer, force_terminal=True, width=100), streams=True)

    async def fake_process_turn(messages: list[ChatCompletionMessageParam]) -> str:
        return "Answer"

    await run_repl(
        process_turn_fn=fake_process_turn,  # type: ignore[arg-type]
        one_shot_prompt="Hello one shot",
        mode="default",
        ui=ui,
    )

    assert "█" in buffer.getvalue()


@pytest.mark.asyncio
async def test_run_repl_one_shot_stays_quiet_when_piped() -> None:
    """Piped one-shot output is consumed by other programs, so no chrome may precede it."""
    buffer = io.StringIO()
    ui = ConsoleUI(console=Console(file=buffer, force_terminal=False, width=100), streams=False)

    async def fake_process_turn(messages: list[ChatCompletionMessageParam]) -> str:
        return "Answer"

    await run_repl(
        process_turn_fn=fake_process_turn,  # type: ignore[arg-type]
        one_shot_prompt="Hello one shot",
        mode="default",
        ui=ui,
    )

    out = buffer.getvalue()
    assert "█" not in out
    assert "nikola" not in out
    assert "Answer" in out


@pytest.mark.asyncio
async def test_run_repl_one_shot_non_streaming_ui_prints_answer() -> None:
    """With --no-stream the loop renders nothing, so the UI prints the returned answer."""
    buffer = io.StringIO()
    ui = ConsoleUI(console=Console(file=buffer, force_terminal=False, width=100), streams=False)

    async def fake_process_turn(messages: list[ChatCompletionMessageParam]) -> str:
        return "Complete answer"

    await run_repl(
        process_turn_fn=fake_process_turn,  # type: ignore[arg-type]
        one_shot_prompt="Hello one shot",
        mode="default",
        ui=ui,
    )

    assert buffer.getvalue().count("Complete answer") == 1
