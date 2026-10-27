"""Filesystem tools for the agent sandbox.

Provides: create_folder, write_file, read_file, modify_file, list_files, search_text,
search_and_replace, glob, grep, update_todo_list.

All file operations are restricted to the workspace root. Delete operations are
intentionally absent — the agent CANNOT delete files or folders.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from openai.types.chat import ChatCompletionToolParam


class FilesystemTool(TypedDict):
    """Definition bundle for a filesystem tool."""

    name: str
    fn: Callable[..., str]
    schema: ChatCompletionToolParam


def get_workspace_root(config_workspace: str | Path = "/workspace") -> Path:
    """get_workspace_root resolves the workspace root path.

    Checks AGENT_WORKSPACE environment variable first (12-factor override).
    Falls back to config_workspace (defaulting to /workspace).

    config_workspace: Workspace directory path from configuration.

    Returns:
        Resolved absolute Path to the workspace root.

    Example:
        root = get_workspace_root("/workspace")
    """
    workspace_env = os.environ.get("AGENT_WORKSPACE")
    if workspace_env:
        return Path(workspace_env).resolve()
    return Path(config_workspace).resolve()


def resolve_safe_path(rel_path: str) -> Path:
    """Resolve a relative path against workspace root and ensure it does not escape.

    Raises PermissionError if path resolves outside the workspace sandbox.
    """
    root = get_workspace_root()
    p = Path(rel_path)
    if p.is_absolute():
        try:
            target = p.resolve()
            if not (target == root or root in target.parents):
                raise PermissionError(f"Access denied: Path '{rel_path}' escapes workspace sandbox '{root}'")
            return target
        except Exception:
            raise PermissionError(f"Access denied: Path '{rel_path}' escapes workspace sandbox '{root}'")
    
    target = (root / rel_path).resolve()
    if not (target == root or root in target.parents):
        raise PermissionError(f"Access denied: Path '{rel_path}' escapes workspace sandbox '{root}'")
    return target


def create_folder(path: str) -> str:
    """create_folder creates a directory at the given path within the workspace.

    path: Relative path to the folder to create.

    Returns a status string.
    """
    target = resolve_safe_path(path)
    target.mkdir(parents=True, exist_ok=True)
    return f"Created directory: {path}"


def write_file(path: str, content: str) -> str:
    """write_file writes string content to a file, creating parent directories if needed.

    path: Relative path to the file.
    content: The text content to write.

    Returns a status string.
    """
    target = resolve_safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"


def read_file(path: str) -> str:
    """read_file reads and returns the full text content of a file.

    path: Relative path to the file.

    Returns the string contents of the file.
    """
    target = resolve_safe_path(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Path is not a file: {path}")
    with target.open("r", encoding="utf-8") as f:
        return f.read()


def modify_file(path: str, old_string: str, new_string: str) -> str:
    """modify_file replaces occurrences of old_string with new_string in a file.

    path: Relative path to the file.
    old_string: The substring to replace.
    new_string: The replacement text.

    Returns a status string.
    """
    target = resolve_safe_path(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with target.open("r", encoding="utf-8") as f:
        content = f.read()
    if old_string not in content:
        raise ValueError(f"Pattern '{old_string}' not found in {path}")
    new_content = content.replace(old_string, new_string)
    with target.open("w", encoding="utf-8") as f:
        f.write(new_content)
    return f"Successfully modified {path}"


def list_files(path: str = ".") -> str:
    """list_files lists files and directories in the given path.

    path: Relative path of directory to inspect. Defaults to workspace root.

    Returns formatted directory listing string.
    """
    target = resolve_safe_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path}")

    items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    if not items:
        return f"Empty directory: {path}"

    lines = []
    for item in items:
        kind = "[DIR]" if item.is_dir() else "[FILE]"
        lines.append(f"{kind} {item.name}")
    return "\n".join(lines)


def search_text(pattern: str, path: str = ".") -> str:
    """search_text searches for lines containing pattern across text files in workspace.

    pattern: Substring to search for.
    path: Relative root path to start search from. Defaults to '.'.

    Returns formatted matches with relative paths and line contents.
    """
    root = resolve_safe_path(path)
    matches = []
    workspace_root = get_workspace_root()

    if root.is_file():
        files = [root]
    else:
        files = [p for p in root.rglob("*") if p.is_file()]

    for file_path in files:
        try:
            rel = file_path.relative_to(workspace_root)
            with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                for idx, line in enumerate(f, 1):
                    if pattern in line:
                        matches.append(f"{rel}:{idx}: {line.rstrip()}")
        except Exception:  # noqa: S112
            continue

    if not matches:
        return f"No matches found for '{pattern}'"
    return "\n".join(matches)


def search_and_replace(path: str, old_string: str, new_string: str) -> str:
    """search_and_replace replaces the first occurrence of old_string with new_string in a file.

    Behaves like a surgical edit: only the first match is replaced, leaving the rest of
    the file untouched. Use modify_file when all occurrences should be replaced.

    path: Relative path to the file to edit.
    old_string: The exact substring to find (must be present exactly once).
    new_string: The replacement text.

    Returns a status string including the line number of the replacement.

    Raises FileNotFoundError if the file does not exist.
    Raises ValueError if old_string is not found in the file.
    """
    target = resolve_safe_path(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    with target.open("r", encoding="utf-8") as f:
        content = f.read()
    if old_string not in content:
        raise ValueError(f"Pattern not found in {path}: {old_string!r}")
    new_content = content.replace(old_string, new_string, 1)
    line_no = content[: content.index(old_string)].count("\n") + 1
    with target.open("w", encoding="utf-8") as f:
        f.write(new_content)
    return f"Replaced at line {line_no} in {path}"


def glob(pattern: str, path: str = ".") -> str:
    """glob lists files matching a shell glob pattern inside the workspace.

    Delegates to pathlib's native glob, so '**' is fully supported for recursive
    matching. Only files (not directories) are returned.

    pattern: Shell glob pattern, e.g. '**/*.py' or 'src/*.txt'.
    path: Relative root directory to search from. Defaults to workspace root ('.').

    Returns a newline-separated list of matching paths relative to the workspace root,
    or a 'No matches' message when nothing is found.

    Example:
        glob("**/*.md")        # all Markdown files
        glob("*.txt", "data")  # text files directly in data/
    """
    root = resolve_safe_path(path)
    workspace_root = get_workspace_root()
    matches = sorted(
        str(p.relative_to(workspace_root))
        for p in root.glob(pattern)
        if p.is_file()
    )
    if not matches:
        return f"No matches for pattern '{pattern}'"
    return "\n".join(matches)


def grep(pattern: str, path: str = ".", flags: str = "") -> str:
    """grep searches files for lines matching a regular expression.

    pattern: Python-compatible regular expression to search for.
    path: Relative root file or directory to search from. Defaults to '.'.
    flags: Optional modifier flags — 'i' for case-insensitive, 'm' for multiline.

    Returns matching lines formatted as '<relpath>:<lineno>: <line>', or a 'No matches'
    message when nothing is found. Binary files are silently skipped.

    Example:
        grep("def \\w+", "agent")      # find function definitions
        grep("error", "logs", "i")     # case-insensitive search in logs/
    """
    root = resolve_safe_path(path)
    workspace_root = get_workspace_root()

    re_flags = 0
    for flag in flags.lower():
        if flag == "i":
            re_flags |= re.IGNORECASE
        elif flag == "m":
            re_flags |= re.MULTILINE

    try:
        compiled = re.compile(pattern, re_flags)
    except re.error as err:
        raise ValueError(f"Invalid regex pattern: {err}") from err

    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())

    matches: list[str] = []
    for file_path in files:
        try:
            rel = file_path.relative_to(workspace_root)
            with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    if compiled.search(line):
                        matches.append(f"{rel}:{lineno}: {line.rstrip()}")
        except Exception:  # noqa: S112
            continue

    if not matches:
        return f"No matches for pattern '{pattern}'"
    return "\n".join(matches)


# ---------------------------------------------------------------------------
# Task planning tool
# ---------------------------------------------------------------------------

_TODO_FILE = ".agent_todo.md"
"""Workspace-relative path of the persistent todo list."""


def update_todo_list(todos: str) -> str:
    """update_todo_list writes (or overwrites) the agent's persistent task plan.

    Stores a markdown checklist as .agent_todo.md at the workspace root so the
    plan survives across turns and can be read back via read_file.

    Treat each line as one task prefixed with '[ ]' (pending), '[x]' (done), or
    '[-]' (in-progress). The entire content is replaced on each call — always
    pass the full, up-to-date list.

    todos: Full markdown checklist content.

    Returns a status string showing how many tasks were recorded.

    Example:
        update_todo_list("[ ] Create output folder\\n[ ] Write numbers.md\\n[x] Verify content")
    """
    target = resolve_safe_path(_TODO_FILE)
    with target.open("w", encoding="utf-8") as f:
        f.write(todos)
    task_count = sum(1 for line in todos.splitlines() if line.strip().startswith(("[", "- [")))
    return f"Todo list updated: {task_count} tasks written to {_TODO_FILE}"


def get_filesystem_tools(workspace: str | None = None) -> list[FilesystemTool]:
    """Return all 10 filesystem tools paired with their OpenAI function schemas.

    workspace: unused — present so callers can pass a workspace path without
               error. The actual root is resolved via AGENT_WORKSPACE env var
               or the /workspace default inside get_workspace_root().
    """
    tools: list[FilesystemTool] = [
        {
            "name": "create_folder",
            "fn": create_folder,
            "schema": {
                "type": "function",
                "function": {
                    "name": "create_folder",
                    "description": "Create a directory folder at the specified path inside the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The relative directory path to create.",
                            }
                        },
                        "required": ["path"],
                    },
                },
            },
        },
        {
            "name": "write_file",
            "fn": write_file,
            "schema": {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write or overwrite text content to a file inside the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The relative file path to write to.",
                            },
                            "content": {
                                "type": "string",
                                "description": "The full text content to write into the file.",
                            },
                        },
                        "required": ["path", "content"],
                    },
                },
            },
        },
        {
            "name": "read_file",
            "fn": read_file,
            "schema": {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the text content of a file inside the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The relative file path to read.",
                            }
                        },
                        "required": ["path"],
                    },
                },
            },
        },
        {
            "name": "modify_file",
            "fn": modify_file,
            "schema": {
                "type": "function",
                "function": {
                    "name": "modify_file",
                    "description": "Modify a file by replacing occurrences of old_string with new_string.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The relative file path to modify.",
                            },
                            "old_string": {
                                "type": "string",
                                "description": "The exact string to find and replace.",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "The replacement string.",
                            },
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                },
            },
        },
        {
            "name": "list_files",
            "fn": list_files,
            "schema": {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files and sub-folders in a directory within the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The relative directory path to list. Defaults to '.'.",
                                "default": ".",
                            }
                        },
                    },
                },
            },
        },
        {
            "name": "search_text",
            "fn": search_text,
            "schema": {
                "type": "function",
                "function": {
                    "name": "search_text",
                    "description": "Search for text occurrences across files within the workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Text pattern or substring to search for.",
                            },
                            "path": {
                                "type": "string",
                                "description": "The relative directory or file to search in. Defaults to '.'.",
                                "default": ".",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
        },
    ]

    # -----------------------------------------------------------------------
    # search_and_replace
    # -----------------------------------------------------------------------
    tools.append(
        {
            "name": "search_and_replace",
            "fn": search_and_replace,
            "schema": {
                "type": "function",
                "function": {
                    "name": "search_and_replace",
                    "description": (
                        "Surgically edit a file by replacing the FIRST occurrence of "
                        "old_string with new_string. The file must exist and old_string "
                        "must be present. Use this as the primary file-edit tool."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative path to the file.",
                            },
                            "old_string": {
                                "type": "string",
                                "description": "The exact string to find (first occurrence is replaced).",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "The replacement string.",
                            },
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                },
            },
        }
    )

    # -----------------------------------------------------------------------
    # glob
    # -----------------------------------------------------------------------
    tools.append(
        {
            "name": "glob",
            "fn": glob,
            "schema": {
                "type": "function",
                "function": {
                    "name": "glob",
                    "description": (
                        "Find files matching a shell glob pattern inside the workspace. "
                        "Supports '**' for recursive matching, e.g. '**/*.py'. "
                        "Returns newline-separated relative paths."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Shell glob pattern, e.g. '**/*.md' or 'src/*.txt'.",
                            },
                            "path": {
                                "type": "string",
                                "description": "Root directory to search from (default '.').",
                                "default": ".",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
        }
    )

    # -----------------------------------------------------------------------
    # grep
    # -----------------------------------------------------------------------
    tools.append(
        {
            "name": "grep",
            "fn": grep,
            "schema": {
                "type": "function",
                "function": {
                    "name": "grep",
                    "description": (
                        "Search files for lines matching a regular expression. "
                        "Returns '<relpath>:<lineno>: <line>' for each match. "
                        "Binary files are silently skipped."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Python-compatible regular expression.",
                            },
                            "path": {
                                "type": "string",
                                "description": "Root file or directory to search (default '.').",
                                "default": ".",
                            },
                            "flags": {
                                "type": "string",
                                "description": "Optional flags: 'i' (case-insensitive), 'm' (multiline).",
                                "default": "",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
        }
    )

    # -----------------------------------------------------------------------
    # update_todo_list
    # -----------------------------------------------------------------------
    tools.append(
        {
            "name": "update_todo_list",
            "fn": update_todo_list,
            "schema": {
                "type": "function",
                "function": {
                    "name": "update_todo_list",
                    "description": (
                        "Write or overwrite the agent's persistent task plan as a markdown "
                        "checklist stored at .agent_todo.md. Call this at the start of a "
                        "multi-step task and after completing each step. Always pass the "
                        "FULL updated list — previous content is replaced."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "todos": {
                                "type": "string",
                                "description": (
                                    "Full markdown checklist. Each task on its own line, prefixed "
                                    "with '[ ]' (pending), '[x]' (done), or '[-]' (in-progress)."
                                ),
                            }
                        },
                        "required": ["todos"],
                    },
                },
            },
        }
    )

    return tools
