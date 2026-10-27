"""Command execution tool for the agent sandbox.

Provides: execute_command — run a shell command with the workspace as its working directory.

The container is the outer sandbox; this module is the inner one. Two harness guarantees
would be void the moment a shell is handed to the model, so they are enforced here rather
than trusted to the prompt:

  - **No deletion.** `rm`, `rmdir`, `shred`, `find … -delete` and friends are rejected
    before the shell ever sees the command, so the "agent cannot delete files or folders"
    rule of the filesystem tools survives shell access.
  - **No path escape.** The command runs with its working directory pinned inside the
    workspace, resolved through the same resolve_safe_path guard the filesystem tools use.

Every run is bounded: a wall-clock timeout kills the whole process group, output is
truncated so a chatty command cannot flood the context window, and secret-looking
environment variables (the InnKube token above all) are stripped from the child process.

The screen is a guardrail, not a proof: a command that hides a deletion inside a script
(`python3 wipe.py`) cannot be caught by inspecting the command line, and the container
boundary remains the real security perimeter.

See also: agent.tools.filesystem (resolve_safe_path, the sandbox root), agent.tools.ToolRegistry.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import signal
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import TypedDict

from openai.types.chat import ChatCompletionToolParam

from agent.tools.filesystem import resolve_safe_path

DEFAULT_TIMEOUT_SECONDS = 30
"""Wall-clock budget for one command when the caller does not ask for another."""

MAX_TIMEOUT_SECONDS = 120
"""Hard ceiling on the timeout, so no single tool call can stall the loop indefinitely."""

MAX_STREAM_CHARS = 5000
"""Per-stream output cap; anything beyond is dropped with a truncation note."""

DENIED_COMMANDS = frozenset(
    {
        # Deletion — the harness promises the agent cannot delete files or folders.
        "rm",
        "rmdir",
        "unlink",
        "shred",
        "srm",
        "trash",
        "trash-put",
        # Privilege escalation.
        "sudo",
        "su",
        "doas",
        # Host and process control.
        "kill",
        "killall",
        "pkill",
        "reboot",
        "shutdown",
        "halt",
        "poweroff",
        "systemctl",
        "service",
        # Device-level writes.
        "dd",
        "mkfs",
        "fdisk",
        "mount",
        "umount",
        # Indirection that would hide any of the above from this screen.
        "eval",
        "exec",
        "source",
    }
)
"""Executables refused outright, matched on the basename of any word in the command."""

DENIED_FLAGS = frozenset({"-delete", "--delete", "--remove-files", "--remove-source-files"})
"""Flags that make an otherwise harmless command (find, rsync) delete files."""

_SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "CREDENTIAL")
"""Substrings marking an environment variable as a secret the child must not see."""

_SUBSTITUTION_CHARS = re.compile(r"[`$(){}]")
"""Shell substitution punctuation, blanked before screening so `$(rm x)` cannot smuggle a word past it."""


class ExecuteTool(TypedDict):
    """Definition bundle for an execution tool: name, async implementation, and schema."""

    name: str
    fn: Callable[..., Awaitable[str]]
    schema: ChatCompletionToolParam


def screen_command(command: str) -> str | None:
    """Check a command line for words the harness refuses to run.

    Splits the command into shell words — operators (`;`, `&&`, `|`) included, so every
    segment of a pipeline is screened, not just the first — and rejects any word whose
    basename is in DENIED_COMMANDS or that is a deletion flag from DENIED_FLAGS. Quoting
    is honoured, so `echo "rm -rf /"` passes: a quoted string is data, not a command.
    Substitution punctuation is blanked first so `$(rm x)` and backticks are screened too.

    command: The raw command line as the model wrote it.

    Returns:
        None when the command may run, otherwise a short reason for refusing it.

    Example:
        screen_command("python3 fetch.py")   # -> None
        screen_command("rm -rf data")        # -> "'rm' is not permitted ..."

    See also: execute_command, which raises PermissionError on a non-None result.
    """
    normalized = _SUBSTITUTION_CHARS.sub(" ", command)
    try:
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError as err:
        return f"could not parse command ({err})"

    for word in words:
        if word.startswith("-"):
            if word.lower() in DENIED_FLAGS:
                return f"flag '{word}' deletes files, which this agent is not allowed to do"
            continue
        name = PurePosixPath(word).name.lower()
        if name in DENIED_COMMANDS:
            return f"'{name}' is not permitted — the agent may not delete files or control the host"
    return None


def child_environment() -> dict[str, str]:
    """Build the environment for a child process, minus anything that looks like a secret.

    A shell tool is an exfiltration path by default: `env` would print the InnKube token
    straight into the transcript. Variables whose name contains a marker such as TOKEN or
    SECRET are dropped; PATH, HOME and the rest are passed through so commands still work.

    Returns:
        A copy of os.environ with secret-looking entries removed.

    See also: _SECRET_ENV_MARKERS.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _SECRET_ENV_MARKERS)
    }


def truncate_stream(text: str, limit: int = MAX_STREAM_CHARS) -> str:
    """Cut captured output to a token-safe length, noting how much was dropped."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"{text[:limit]}\n[... {dropped} more characters truncated]"


def format_result(exit_code: int, stdout: str, stderr: str) -> str:
    """Render a finished command as one observation string.

    Always reports the exit code — the model needs the success signal as much as the
    output — then each non-empty stream under its own header. A silent successful command
    reports "(no output)" rather than an empty observation.

    exit_code: Process return code.
    stdout: Captured standard output (already truncated).
    stderr: Captured standard error (already truncated).

    Returns:
        The observation string handed back to the loop.
    """
    sections = [f"Exit code: {exit_code}"]
    if stdout.strip():
        sections.append(f"--- stdout ---\n{stdout.rstrip()}")
    if stderr.strip():
        sections.append(f"--- stderr ---\n{stderr.rstrip()}")
    if len(sections) == 1:
        sections.append("(no output)")
    return "\n".join(sections)


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Terminate a timed-out process and everything it spawned.

    The child is started in its own session, so signalling the process group also reaps
    grandchildren (a shell pipeline leaves several). SIGTERM first, then SIGKILL if the
    process is still alive a moment later.
    """

    def signal_group(sig: int) -> None:
        if hasattr(os, "killpg"):
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(proc.pid), sig)
                return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()

    signal_group(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except TimeoutError:
        signal_group(signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()


async def execute_command(
    command: str,
    cwd: str = ".",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """execute_command runs a shell command inside the workspace and returns its output.

    The command is screened first (see screen_command) and refused if it could delete
    files or control the host. It then runs through the system shell — so pipes,
    redirection and globbing work — with the working directory pinned inside the
    workspace, secrets stripped from the environment, and a wall-clock timeout that kills
    the whole process group. Both streams are captured and truncated.

    A non-zero exit code is a normal result, not an exception: the exit code and stderr
    come back as the observation so the model can reason about the failure.

    command: The shell command line to run, e.g. "python3 script.py --json".
    cwd: Workspace-relative directory to run in. Defaults to the workspace root.
    timeout_seconds: Wall-clock budget, clamped to 1..MAX_TIMEOUT_SECONDS.

    Returns:
        "Exit code: N" followed by the stdout and stderr sections that are non-empty,
        or a timeout message when the budget was exhausted.

    Raises:
        ValueError: command is empty.
        PermissionError: the command is refused by the screen, or cwd escapes the workspace.
        NotADirectoryError: cwd does not exist or is not a directory.

    Example:
        await execute_command("python3 .nikola/skills/mensa-passau/fetch-mensa.py")

    See also: screen_command, get_execute_tools.
    """
    if not command or not command.strip():
        raise ValueError("command must not be empty")

    refusal = screen_command(command)
    if refusal is not None:
        raise PermissionError(f"Command refused: {refusal}")

    working_dir = resolve_safe_path(cwd)  # raises PermissionError on sandbox escape
    if not working_dir.is_dir():
        raise NotADirectoryError(f"Working directory not found: {cwd}")

    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(working_dir),
        env=child_environment(),
        start_new_session=True,  # own process group, so a timeout can reap the whole tree
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _kill_process_group(proc)
        return f"Error: command timed out after {timeout}s and was killed: {command}"

    stdout = truncate_stream(stdout_bytes.decode("utf-8", errors="replace"))
    stderr = truncate_stream(stderr_bytes.decode("utf-8", errors="replace"))
    return format_result(proc.returncode or 0, stdout, stderr)


def get_execute_tools() -> list[ExecuteTool]:
    """Return the execution tools paired with their OpenAI function schemas.

    Returns:
        A single-entry list holding execute_command, shaped like get_filesystem_tools'
        output so make_default_tool_registry can register both the same way.

    See also: agent.tools.make_default_tool_registry.
    """
    denied_preview = ", ".join(sorted(DENIED_COMMANDS)[:8])
    return [
        {
            "name": "execute_command",
            "fn": execute_command,
            "schema": {
                "type": "function",
                "function": {
                    "name": "execute_command",
                    "description": (
                        "Run a shell command inside the workspace and return its exit code, "
                        "stdout and stderr. Use it to run scripts (e.g. 'python3 script.py') "
                        "or inspect the environment. Commands run from the workspace root with "
                        "a timeout; deletion and host-control commands "
                        f"({denied_preview}, …) are refused, as are working directories outside "
                        "the workspace. To read, write or search files prefer the dedicated "
                        "filesystem tools — they are cheaper and always allowed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command line to run, e.g. 'python3 fetch.py'.",
                            },
                            "cwd": {
                                "type": "string",
                                "description": "Workspace-relative directory to run in (default '.').",
                                "default": ".",
                            },
                            "timeout_seconds": {
                                "type": "integer",
                                "description": (
                                    f"Wall-clock limit in seconds (default {DEFAULT_TIMEOUT_SECONDS}, "
                                    f"max {MAX_TIMEOUT_SECONDS})."
                                ),
                                "default": DEFAULT_TIMEOUT_SECONDS,
                            },
                        },
                        "required": ["command"],
                    },
                },
            },
        }
    ]
