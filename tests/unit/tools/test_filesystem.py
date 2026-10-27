"""Unit tests for agent.tools.filesystem sandbox tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tools import ToolRegistry
from agent.tools.filesystem import (
    create_folder,
    get_filesystem_tools,
    glob,
    grep,
    list_files,
    modify_file,
    read_file,
    resolve_safe_path,
    search_and_replace,
    search_text,
    update_todo_list,
    write_file,
)


def test_resolve_safe_path_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relative paths inside workspace resolve correctly."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    resolved = resolve_safe_path("sub/file.txt")
    assert resolved == (tmp_path / "sub" / "file.txt").resolve()


def test_resolve_safe_path_escapes_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Paths attempting to escape the workspace sandbox raise PermissionError."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    with pytest.raises(PermissionError):
        resolve_safe_path("../outside.txt")
    with pytest.raises(PermissionError):
        resolve_safe_path("/etc/passwd")


def test_create_folder_and_list_files_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """create_folder makes directories; list_files on empty dir returns 'Empty directory' or empty listing."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    res_mkdir = create_folder("my_dir")
    assert "Created directory" in res_mkdir

    res_list = list_files("my_dir")
    assert "Empty directory" in res_list or res_list == ""


def test_write_read_modify_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """write_file creates file, read_file reads content, modify_file replaces string or rewrites."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    res_write = write_file("test.txt", "Hello World\nLine 2")
    assert "Wrote 18 bytes" in res_write or "Successfully" in res_write

    res_read = read_file("test.txt")
    assert res_read == "Hello World\nLine 2"

    res_mod = modify_file("test.txt", "World", "Universe")
    assert "Successfully" in res_mod
    assert read_file("test.txt") == "Hello Universe\nLine 2"


def test_search_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_text finds matching lines across files."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("a.txt", "alpha\nbeta\none\ntwenty")
    write_file("b.txt", "gamma\none")

    res = search_text("one")
    assert "a.txt" in res
    assert "b.txt" in res


def test_get_filesystem_tools_registers_all_into_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_filesystem_tools returns all 10 tools with schemas that can be registered into ToolRegistry."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    registry = ToolRegistry()
    tools = get_filesystem_tools()
    assert len(tools) == 10

    for tool in tools:
        registry.register(tool["name"], tool["fn"], tool["schema"])

    schemas = registry.get_schemas()
    schema_names = [s["function"]["name"] for s in schemas]
    expected = [
        "create_folder", "write_file", "read_file", "modify_file",
        "list_files", "search_text", "search_and_replace", "glob",
        "grep", "update_todo_list",
    ]
    for exp in expected:
        assert exp in schema_names
    assert "delete_file" not in schema_names
    assert "delete_folder" not in schema_names


# ---------------------------------------------------------------------------
# search_and_replace
# ---------------------------------------------------------------------------

def test_search_and_replace_replaces_first_occurrence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_and_replace replaces only the first match and reports the line number."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("edit.txt", "foo bar\nfoo baz\n")

    result = search_and_replace("edit.txt", "foo", "qux")
    assert "line 1" in result
    content = read_file("edit.txt")
    # First occurrence replaced, second untouched
    assert content == "qux bar\nfoo baz\n"


def test_search_and_replace_missing_pattern_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_and_replace raises ValueError when old_string is not found."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("edit.txt", "hello world")
    with pytest.raises(ValueError, match="Pattern not found"):
        search_and_replace("edit.txt", "missing", "x")


def test_search_and_replace_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_and_replace raises FileNotFoundError when the file does not exist."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        search_and_replace("no_such.txt", "x", "y")


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------

def test_glob_matches_files_by_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """glob returns only files that match the given pattern."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("notes.md", "# notes")
    write_file("readme.txt", "readme")
    write_file("sub/doc.md", "# sub")

    result = glob("**/*.md")
    assert "notes.md" in result
    assert "sub/doc.md" in result
    assert "readme.txt" not in result


def test_glob_no_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """glob returns a 'No matches' message when nothing fits the pattern."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("a.txt", "data")
    result = glob("**/*.py")
    assert "No matches" in result


def test_glob_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """glob on an empty workspace returns a 'No matches' message."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = glob("**/*")
    assert "No matches" in result


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------

def test_grep_finds_matching_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """grep returns file:lineno: line for every matching line."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("src/main.py", "def hello():\n    pass\n")
    write_file("src/util.py", "def helper():\n    return 1\n")

    result = grep(r"def \w+", "src")
    assert "main.py" in result
    assert "util.py" in result
    assert "def hello" in result
    assert "def helper" in result


def test_grep_case_insensitive_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """grep with flag 'i' performs case-insensitive search."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("log.txt", "ERROR: something\ninfo: normal\n")

    result = grep("error", "log.txt", flags="i")
    assert "ERROR" in result


def test_grep_no_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """grep returns a 'No matches' message when the pattern is not found."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    write_file("a.txt", "hello world")
    result = grep("xyz123")
    assert "No matches" in result


def test_grep_invalid_regex_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """grep raises ValueError for an invalid regular expression."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid regex"):
        grep("[unclosed")


def test_grep_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """grep on an empty workspace returns a 'No matches' message without errors."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    result = grep("anything")
    assert "No matches" in result


# ---------------------------------------------------------------------------
# update_todo_list
# ---------------------------------------------------------------------------

def test_update_todo_list_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """update_todo_list persists the checklist to .agent_todo.md."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    checklist = "[ ] step one\n[x] step two\n[-] step three"
    result = update_todo_list(checklist)
    assert "3 tasks" in result
    persisted = read_file(".agent_todo.md")
    assert persisted == checklist


def test_update_todo_list_overwrites_previous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """update_todo_list replaces the previous content on subsequent calls."""
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    update_todo_list("[ ] old task")
    update_todo_list("[ ] new task one\n[ ] new task two")
    persisted = read_file(".agent_todo.md")
    assert "old task" not in persisted
    assert "new task two" in persisted
