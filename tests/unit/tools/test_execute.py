"""Unit tests for agent.tools.execute — the sandboxed command execution tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tools import make_default_tool_registry
from agent.tools.execute import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_STREAM_CHARS,
    child_environment,
    execute_command,
    format_result,
    get_execute_tools,
    screen_command,
    truncate_stream,
)

# ---------------------------------------------------------------------------
# screen_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python3 script.py",
        "ls -la",
        "echo hello | wc -l",
        "cat notes.txt && head -n 2 notes.txt",
        "/usr/bin/python3 --version",
    ],
)
def test_screen_command_allows_harmless_commands(command: str) -> None:
    """Ordinary read-only and script-running commands pass the screen."""
    assert screen_command(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "rm file.txt",
        "rm -rf /workspace",
        "/bin/rm file.txt",
        "rmdir mydir",
        "shred secrets.txt",
        "ls && rm file.txt",
        "ls; rm file.txt",
        "find . -name '*.txt' -exec rm {} ;",
        "find . -name '*.txt' -delete",
        "echo file.txt | xargs rm",
        "sudo apt-get install curl",
        "eval something",
        "$(rm file.txt)",
        "`rm file.txt`",
    ],
)
def test_screen_command_refuses_destructive_commands(command: str) -> None:
    """Deletion, escalation, and substitution-smuggled deletion are all refused."""
    assert screen_command(command) is not None


def test_screen_command_allows_quoted_words() -> None:
    """A denied word inside quotes is data, not a command, so it is allowed."""
    assert screen_command('echo "rm -rf /"') is None


def test_screen_command_reports_unparseable_command() -> None:
    """An unbalanced quote is refused rather than passed to the shell."""
    assert screen_command('echo "unterminated') is not None


# ---------------------------------------------------------------------------
# execute_command — happy path
# ---------------------------------------------------------------------------


async def test_execute_command_returns_stdout_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful command reports exit code 0 and its stdout."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = await execute_command("echo hello")
    assert "Exit code: 0" in result
    assert "hello" in result


async def test_execute_command_runs_in_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The working directory defaults to the workspace root, not the process cwd."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    result = await execute_command("ls")
    assert "marker.txt" in result


async def test_execute_command_honours_relative_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace-relative cwd is resolved and used."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inner.txt").write_text("x", encoding="utf-8")
    result = await execute_command("ls", cwd="sub")
    assert "inner.txt" in result


async def test_execute_command_runs_a_python_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running a script in the workspace works — the case skills rely on."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    (tmp_path / "hello.py").write_text('print("from script")', encoding="utf-8")
    result = await execute_command("python3 hello.py")
    assert "Exit code: 0" in result
    assert "from script" in result


async def test_execute_command_reports_failure_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-zero exit is an observation, not an exception: code and stderr come back."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = await execute_command("python3 -c 'import sys; sys.stderr.write(\"boom\"); sys.exit(3)'")
    assert "Exit code: 3" in result
    assert "boom" in result


async def test_execute_command_reports_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A silent command still yields a readable observation."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = await execute_command("true")
    assert "Exit code: 0" in result
    assert "(no output)" in result


# ---------------------------------------------------------------------------
# execute_command — guards
# ---------------------------------------------------------------------------


async def test_execute_command_refuses_deletion_and_leaves_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused command never reaches the shell, so the target file survives."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    victim = tmp_path / "keep.txt"
    victim.write_text("still here", encoding="utf-8")

    with pytest.raises(PermissionError):
        await execute_command("rm keep.txt")

    assert victim.exists()


async def test_execute_command_rejects_cwd_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A working directory outside the workspace is a sandbox escape."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    with pytest.raises(PermissionError):
        await execute_command("ls", cwd="..")
    with pytest.raises(PermissionError):
        await execute_command("ls", cwd="/etc")


async def test_execute_command_rejects_missing_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cwd inside the workspace that does not exist is reported clearly."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    with pytest.raises(NotADirectoryError):
        await execute_command("ls", cwd="nowhere")


async def test_execute_command_rejects_empty_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty command is a caller error, not a shell no-op."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError):
        await execute_command("   ")


async def test_execute_command_times_out_and_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command that outlives its budget is killed and reported as an error."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = await execute_command("sleep 30", timeout_seconds=1)
    assert result.startswith("Error: command timed out after 1s")


async def test_execute_command_strips_secrets_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Secret-looking env vars are not visible to the child process."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("INNKUBE_ACCESS_TOKEN", "super-secret-value")
    monkeypatch.setenv("HARMLESS_SETTING", "visible-value")

    # Grep for each name so the assertion does not depend on how large the real env is.
    secret = await execute_command("env | grep INNKUBE_ACCESS_TOKEN")
    assert "super-secret-value" not in secret

    harmless = await execute_command("env | grep HARMLESS_SETTING")
    assert "visible-value" in harmless


def test_child_environment_drops_secret_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """child_environment filters by name marker and keeps everything else."""
    monkeypatch.setenv("MY_API_KEY", "k")
    monkeypatch.setenv("SOME_PASSWORD", "p")
    monkeypatch.setenv("PLAIN_SETTING", "v")

    env = child_environment()
    assert "MY_API_KEY" not in env
    assert "SOME_PASSWORD" not in env
    assert env["PLAIN_SETTING"] == "v"


async def test_execute_command_truncates_long_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chatty command cannot flood the context window."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = await execute_command("python3 -c 'print(\"x\" * 50000)'")
    assert "truncated" in result
    assert len(result) < 50000


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def test_truncate_stream_keeps_short_text() -> None:
    """Text within the limit is returned unchanged."""
    assert truncate_stream("short") == "short"


def test_truncate_stream_notes_dropped_characters() -> None:
    """Truncation says how much was dropped."""
    truncated = truncate_stream("y" * (MAX_STREAM_CHARS + 100))
    assert "100 more characters truncated" in truncated


def test_format_result_includes_both_streams() -> None:
    """Both streams appear under their own headers when non-empty."""
    formatted = format_result(1, "out", "err")
    assert "Exit code: 1" in formatted
    assert "--- stdout ---\nout" in formatted
    assert "--- stderr ---\nerr" in formatted


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def test_execute_tool_schema_is_well_formed() -> None:
    """The schema names the tool, requires a command, and documents its defaults."""
    tools = get_execute_tools()
    assert len(tools) == 1

    schema = tools[0]["schema"]
    function = schema["function"]
    assert function["name"] == "execute_command"
    parameters = function["parameters"]
    assert parameters["required"] == ["command"]
    assert set(parameters["properties"]) == {"command", "cwd", "timeout_seconds"}
    assert parameters["properties"]["timeout_seconds"]["default"] == DEFAULT_TIMEOUT_SECONDS


async def test_registry_dispatches_execute_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool is registered by default and dispatchable through the registry."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    registry = make_default_tool_registry(workspace=str(tmp_path))

    names = [schema["function"]["name"] for schema in registry.get_schemas()]
    assert "execute_command" in names

    result = await registry.dispatch("execute_command", {"command": "echo dispatched"})
    assert "dispatched" in result


async def test_registry_reports_refusal_as_error_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused command reaches the model as an 'Error: ' observation, not a crash."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    registry = make_default_tool_registry(workspace=str(tmp_path))

    result = await registry.dispatch("execute_command", {"command": "rm -rf ."})
    assert result.startswith("Error: Command refused")
