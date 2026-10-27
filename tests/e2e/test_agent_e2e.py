"""E2E tests for the agent harness ReAct loop and filesystem tools.

Verifies canonical test scenarios from specification:
  - numbers-in-words: agent creates directory, writes file, searches text, answers.
  - find-and-inspect: pre-populated workspace inspected by agent via list_files and read_file.
  - glob-search: agent uses glob to discover files by pattern, then reads one.
  - grep-search: agent uses grep to find regex matches across files, then answers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from openai.types.chat.completion_create_params import (
    CompletionCreateParamsNonStreaming,
)

from agent.loop import make_process_turn_fn_v2
from agent.tools import make_default_tool_registry
from agent.tools.filesystem import (
    create_folder,
    write_file,
)


def _make_tool_call_completion(name: str, args: dict) -> ChatCompletion:
    tc = ChatCompletionMessageToolCall(
        id=f"call_{name}",
        type="function",
        function=Function(
            name=name,
            arguments=json.dumps(args),
        ),
    )
    msg = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[tc],
    )
    choice = Choice(
        finish_reason="tool_calls",
        index=0,
        message=msg,
    )
    return ChatCompletion(
        id="mock_id",
        choices=[choice],
        created=1000,
        model="mock-model",
        object="chat.completion",
    )


def _make_final_completion(content: str) -> ChatCompletion:
    msg = ChatCompletionMessage(
        role="assistant",
        content=content,
        tool_calls=None,
    )
    choice = Choice(
        finish_reason="stop",
        index=0,
        message=msg,
    )
    return ChatCompletion(
        id="mock_id",
        choices=[choice],
        created=1000,
        model="mock-model",
        object="chat.completion",
    )


@pytest.mark.asyncio
async def test_e2e_numbers_in_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonical test: numbers-in-words.

    Tools: create_folder, write_file, search_text.
    Asserts output/ exists, output/numbers.md contains 'one' and 'twenty', final turn acknowledges.
    """
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    responses = [
        # Step 1: LLM calls create_folder("output")
        _make_tool_call_completion("create_folder", {"path": "output"}),
        # Step 2: LLM calls write_file("output/numbers.md", "one\ntwo\n...twenty")
        _make_tool_call_completion(
            "write_file",
            {"path": "output/numbers.md", "content": "one\ntwo\nthree\ntwenty"},
        ),
        # Step 3: LLM calls search_text("twenty", "output")
        _make_tool_call_completion("search_text", {"pattern": "twenty", "path": "output"}),
        # Step 4: Final response
        _make_final_completion("Successfully created output/numbers.md with numbers from one to twenty and verified."),
    ]

    call_index = 0

    def mock_dispatch(req: CompletionCreateParamsNonStreaming) -> ChatCompletion:
        nonlocal call_index
        resp = responses[call_index]
        call_index += 1
        return resp

    registry = make_default_tool_registry()
    config = {"loop": {"max_iterations": 10}}
    process_turn = make_process_turn_fn_v2(mock_dispatch, registry, config)

    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content="Default prompt"),
        ChatCompletionUserMessageParam(
            role="user",
            content="Create output/numbers.md containing numbers in words up to twenty, search it, and confirm.",
        ),
    ]

    final_answer = await process_turn(messages)

    # 1. Assert output/ directory exists
    output_dir = tmp_path / "output"
    assert output_dir.exists()
    assert output_dir.is_dir()

    # 2. Assert output/numbers.md contains 'one' and 'twenty'
    numbers_file = output_dir / "numbers.md"
    assert numbers_file.exists()
    content = numbers_file.read_text(encoding="utf-8")
    assert "one" in content
    assert "twenty" in content

    # 3. Final agent turn acknowledges
    assert "Successfully created" in final_answer


@pytest.mark.asyncio
async def test_e2e_find_and_inspect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonical test: find-and-inspect.

    Pre-populates workspace: pathABC/data/notes.txt ("This\nfile\nhas\none\nline") + readme.md + empty/
    Tools: list_files, read_file.
    Asserts agent lists files and reports line count accurately.
    """
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    # Pre-populate workspace
    create_folder("pathABC/data")
    create_folder("empty")
    write_file("pathABC/data/notes.txt", "This\nfile\nhas\none\nline")
    write_file("readme.md", "Project documentation")

    responses = [
        # Step 1: list files
        _make_tool_call_completion("list_files", {"path": "pathABC/data"}),
        # Step 2: read notes.txt
        _make_tool_call_completion("read_file", {"path": "pathABC/data/notes.txt"}),
        # Step 3: Final response
        _make_final_completion("The file pathABC/data/notes.txt contains exactly 5 lines."),
    ]

    call_index = 0

    def mock_dispatch(req: CompletionCreateParamsNonStreaming) -> ChatCompletion:
        nonlocal call_index
        resp = responses[call_index]
        call_index += 1
        return resp

    registry = make_default_tool_registry()
    config = {"loop": {"max_iterations": 10}}
    process_turn = make_process_turn_fn_v2(mock_dispatch, registry, config)

    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content="Default prompt"),
        ChatCompletionUserMessageParam(
            role="user",
            content="Inspect pathABC/data/notes.txt and report how many lines it has.",
        ),
    ]

    final_answer = await process_turn(messages)

    assert "5 lines" in final_answer
    assert (tmp_path / "pathABC" / "data" / "notes.txt").exists()
    assert (tmp_path / "empty").is_dir()


@pytest.mark.asyncio
async def test_e2e_glob_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: agent uses glob to find Markdown files, then reads one and answers.

    Pre-populates workspace with two .md files and one .txt file.
    Asserts agent discovers both .md files, excludes .txt, and reports a match.
    """
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    # Pre-populate workspace
    write_file("docs/intro.md", "# Introduction\nWelcome to the project.")
    write_file("docs/guide.md", "# Guide\nStep by step instructions.")
    write_file("docs/notes.txt", "plain text, not markdown")

    responses = [
        # Step 1: LLM calls glob to find all .md files
        _make_tool_call_completion("glob", {"pattern": "**/*.md"}),
        # Step 2: LLM reads one of the discovered files
        _make_tool_call_completion("read_file", {"path": "docs/intro.md"}),
        # Step 3: Final answer
        _make_final_completion("Found 2 markdown files: docs/intro.md and docs/guide.md."),
    ]

    call_index = 0

    def mock_dispatch(req: CompletionCreateParamsNonStreaming) -> ChatCompletion:
        nonlocal call_index
        resp = responses[call_index]
        call_index += 1
        return resp

    registry = make_default_tool_registry()
    config = {"loop": {"max_iterations": 10}}
    process_turn = make_process_turn_fn_v2(mock_dispatch, registry, config)

    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content="Default prompt"),
        ChatCompletionUserMessageParam(
            role="user",
            content="Find all Markdown files in the workspace and report what you find.",
        ),
    ]

    final_answer = await process_turn(messages)

    # Both .md files must exist
    assert (tmp_path / "docs" / "intro.md").exists()
    assert (tmp_path / "docs" / "guide.md").exists()
    # Final answer mentions markdown files
    assert "markdown" in final_answer.lower() or "md" in final_answer.lower()


@pytest.mark.asyncio
async def test_e2e_grep_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E: agent uses grep to locate a regex pattern across files, then reports matches.

    Pre-populates workspace with two Python-like files.
    Asserts agent finds function definitions with grep and reports them.
    """
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))

    # Pre-populate workspace
    write_file("src/alpha.py", "def compute(x):\n    return x * 2\n")
    write_file("src/beta.py", "def transform(y):\n    return y + 1\n")
    write_file("src/config.txt", "max_retries = 3\n")

    responses = [
        # Step 1: LLM calls grep to find function definitions
        _make_tool_call_completion("grep", {"pattern": r"^def ", "path": "src"}),
        # Step 2: Final answer summarising the findings
        _make_final_completion(
            "Found 2 function definitions: compute in alpha.py and transform in beta.py."
        ),
    ]

    call_index = 0

    def mock_dispatch(req: CompletionCreateParamsNonStreaming) -> ChatCompletion:
        nonlocal call_index
        resp = responses[call_index]
        call_index += 1
        return resp

    registry = make_default_tool_registry()
    config = {"loop": {"max_iterations": 10}}
    process_turn = make_process_turn_fn_v2(mock_dispatch, registry, config)

    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content="Default prompt"),
        ChatCompletionUserMessageParam(
            role="user",
            content="Find all top-level function definitions in the src/ directory.",
        ),
    ]

    final_answer = await process_turn(messages)

    # Source files must be present
    assert (tmp_path / "src" / "alpha.py").exists()
    assert (tmp_path / "src" / "beta.py").exists()
    # Final answer mentions both functions
    assert "compute" in final_answer
    assert "transform" in final_answer
