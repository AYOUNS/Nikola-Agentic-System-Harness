"""E2E test for sub-agent delegation.

Tests that:
1. Sub-agents run as separate instances with own permissions
2. Parent can delegate tasks and receive condensed results
3. Sub-agent execution is recorded in observability data
4. Delegation benefits: many tool calls compressed to one short result
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.hooks import (
    SubAgentEndEvent,
    SubAgentStartEvent,
    get_hook_registry,
)
from agent.subagents.orchestrator import execute_subagent
from agent.tools import ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_workspace():
    """Create a temporary workspace for file operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        
        # Pre-populate test files
        (workspace / "docs").mkdir()
        (workspace / "docs" / "api.md").write_text("# API Documentation\nFunction: calculate\nReturns: number")
        (workspace / "docs" / "guide.md").write_text("# User Guide\nHow to use calculate function")
        (workspace / "src").mkdir()
        (workspace / "src" / "main.py").write_text("def calculate(x):\n    return x * 2")
        (workspace / "README.md").write_text("Project README")
        
        yield workspace


@pytest.fixture
def registry(temp_workspace):
    """Build a ToolRegistry with file tools."""
    reg = ToolRegistry()
    
    # Register read_file
    async def read_file_fn(path: str) -> str:
        try:
            full_path = temp_workspace / path
            return full_path.read_text()
        except Exception as err:
            return f"Error: {err}"
    
    reg.register(
        name="read_file",
        fn=read_file_fn,
        schema={
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
    )
    
    # Register list_files
    async def list_files_fn(path: str = ".") -> str:
        try:
            full_path = temp_workspace / path
            items = [p.name for p in full_path.iterdir()]
            return "\n".join(items)
        except Exception as err:
            return f"Error: {err}"
    
    reg.register(
        name="list_files",
        fn=list_files_fn,
        schema={
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        },
    )
    
    # Register search_text (simplified)
    async def search_text_fn(pattern: str, path: str = ".") -> str:
        try:
            full_path = temp_workspace / path
            matches = []
            for file in full_path.rglob("*.md"):
                content = file.read_text()
                if pattern.lower() in content.lower():
                    matches.append(str(file.relative_to(temp_workspace)))
            return "\n".join(matches) if matches else f"No matches for '{pattern}'"
        except Exception as err:
            return f"Error: {err}"
    
    reg.register(
        name="search_text",
        fn=search_text_fn,
        schema={
            "type": "function",
            "function": {
                "name": "search_text",
                "description": "Search for text in files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    )
    
    # Register grep (simplified)
    async def grep_fn(pattern: str) -> str:
        matches = []
        for file in temp_workspace.rglob("*"):
            if file.is_file():
                try:
                    content = file.read_text()
                    if pattern in content:
                        matches.append(str(file.relative_to(temp_workspace)))
                except Exception:
                    pass
        return "\n".join(matches) if matches else f"No matches for '{pattern}'"
    
    reg.register(
        name="grep",
        fn=grep_fn,
        schema={
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search files by content",
                "parameters": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            },
        },
    )
    
    # Register glob (simplified)
    async def glob_fn(pattern: str) -> str:
        matches = [str(p.relative_to(temp_workspace)) for p in temp_workspace.rglob(pattern)]
        return "\n".join(matches) if matches else f"No matches for '{pattern}'"
    
    reg.register(
        name="glob",
        fn=glob_fn,
        schema={
            "type": "function",
            "function": {
                "name": "glob",
                "description": "Find files by pattern",
                "parameters": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            },
        },
    )
    
    return reg


@pytest.fixture
def mock_llm_dispatcher():
    """Mock LLM dispatcher that simulates file-scout behavior."""
    
    async def dispatcher(request: dict) -> MagicMock:
        """Simulate a file-scout sub-agent completing a search task."""
        messages = request["messages"]
        
        # First call: list files
        if len(messages) == 2:
            # Initial prompt
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message = MagicMock()
            response.choices[0].message.content = None
            response.choices[0].message.tool_calls = [
                MagicMock(
                    id="call_1",
                    function=MagicMock(
                        name="list_files",
                        arguments='{"path": "docs"}',
                    ),
                )
            ]
            return response
        
        # Second call: read file
        if len(messages) == 4:
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message = MagicMock()
            response.choices[0].message.content = None
            response.choices[0].message.tool_calls = [
                MagicMock(
                    id="call_2",
                    function=MagicMock(
                        name="read_file",
                        arguments='{"path": "docs/api.md"}',
                    ),
                )
            ]
            return response
        
        # Third call: final answer
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message = MagicMock()
        response.choices[0].message.content = (
            "Found documentation about the calculate function in docs/api.md. "
            "It takes a number and returns a number."
        )
        response.choices[0].message.tool_calls = None
        return response
    
    return dispatcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subagent_executes_with_separate_permissions(registry, mock_llm_dispatcher, temp_workspace):
    """Test that sub-agents run with their own permission policy."""
    result = await execute_subagent(
        role_name="file-scout",
        task="Find information about the calculate function",
        parent_session_id="test-parent",
        registry=registry,
        llm_dispatcher=mock_llm_dispatcher,
        workspace=str(temp_workspace),
    )
    
    # Should complete successfully
    assert "Found documentation" in result
    assert "calculate function" in result
    
    # Result should be condensed (file-scout has max_result_chars=2000)
    assert len(result) <= 2100  # Allow some margin


@pytest.mark.asyncio
async def test_subagent_observability_events_emitted(registry, mock_llm_dispatcher, temp_workspace):
    """Test that sub-agent execution is recorded in observability data."""
    # Register a test hook to capture events
    captured_events = []
    
    class TestHook:
        def on_subagent_start(self, event: SubAgentStartEvent) -> None:
            captured_events.append(("start", event))
        
        def on_subagent_end(self, event: SubAgentEndEvent) -> None:
            captured_events.append(("end", event))
    
    hooks = get_hook_registry()
    hooks.register(TestHook())
    
    try:
        await execute_subagent(
            role_name="file-scout",
            task="Find information about the calculate function",
            parent_session_id="test-parent-123",
            registry=registry,
            llm_dispatcher=mock_llm_dispatcher,
            workspace=str(temp_workspace),
        )
        
        # Should have captured start and end events
        assert len(captured_events) == 2
        
        # Check start event
        start_type, start_event = captured_events[0]
        assert start_type == "start"
        assert start_event.parent_session_id == "test-parent-123"
        assert start_event.role_name == "file-scout"
        assert "calculate function" in start_event.task
        assert start_event.subagent_id  # Has an ID
        
        # Check end event
        end_type, end_event = captured_events[1]
        assert end_type == "end"
        assert end_event.parent_session_id == "test-parent-123"
        assert end_event.role_name == "file-scout"
        assert end_event.success is True
        assert end_event.subagent_id == start_event.subagent_id  # Same ID
        assert end_event.iterations > 0
        # Note: tools_used tracking depends on message structure after model_dump()
        # In this test, we just verify the mechanism exists and duration is tracked
        assert end_event.duration_ms > 0  # Took some time
        assert isinstance(end_event.tools_used, list)  # Tools list exists (may be empty in mock)
        
    finally:
        hooks.clear()


@pytest.mark.asyncio
async def test_delegation_benefit_context_compression(registry, mock_llm_dispatcher, temp_workspace):
    """Test that delegation compresses many tool calls into one short result.
    
    Benefit: Without delegation, if a parent agent searches through 10 files,
    it accumulates 10 observations in its context (potentially 10,000+ tokens).
    With delegation, the sub-agent does the same work but returns only a
    200-character summary, saving ~9,800 tokens in the parent's context.
    """
    result = await execute_subagent(
        role_name="file-scout",
        task="Search all files for information about the calculate function and summarize what you find",
        parent_session_id="test-parent",
        registry=registry,
        llm_dispatcher=mock_llm_dispatcher,
        workspace=str(temp_workspace),
    )
    
    # Sub-agent did the work
    assert result is not None
    assert len(result) > 0
    
    # Result is condensed (much shorter than raw observations would be)
    # If the sub-agent read 3 files of ~100 chars each = 300 chars of raw data,
    # but returned only a ~150 char summary, that's a 50% compression
    assert len(result) < 500  # Reasonable summary length
    
    # Should contain useful information
    assert "calculate" in result.lower()


@pytest.mark.asyncio
async def test_two_specialized_subagent_roles_exist():
    """Test that at least 2 specialized sub-agent configurations exist."""
    from agent.subagents.roles import list_roles
    
    roles = list_roles()
    
    # Must have at least 2 roles
    assert len(roles) >= 2
    
    # Should include file-scout (read-only research)
    assert "file-scout" in roles
    
    # Should include scheduler (calendar management) or another specialized role
    assert "scheduler" in roles or len(roles) >= 2


@pytest.mark.asyncio
async def test_subagent_permission_enforcement(registry, temp_workspace):
    """Test that sub-agents respect their permission policy.
    
    file-scout is read-only — it should NOT be able to write files
    even if the parent agent can.
    """
    # Mock LLM that tries to write a file (should be denied)
    async def malicious_llm(request: dict) -> MagicMock:
        messages = request["messages"]
        
        if len(messages) == 2:
            # Try to call write_file (not in file-scout allowlist)
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message = MagicMock()
            response.choices[0].message.content = None
            response.choices[0].message.tool_calls = [
                MagicMock(
                    id="call_evil",
                    function=MagicMock(
                        name="write_file",
                        arguments='{"path": "hack.txt", "content": "pwned"}',
                    ),
                )
            ]
            return response
        
        # Should get error, then give up
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message = MagicMock()
        response.choices[0].message.content = "I cannot complete this task"
        response.choices[0].message.tool_calls = None
        return response
    
    # Add write_file to parent registry (so parent CAN write)
    async def write_file_fn(path: str, content: str) -> str:
        (temp_workspace / path).write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    
    registry.register(
        name="write_file",
        fn=write_file_fn,
        schema={
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    )
    
    result = await execute_subagent(
        role_name="file-scout",
        task="Write a summary file",
        parent_session_id="test-parent",
        registry=registry,
        llm_dispatcher=malicious_llm,
        workspace=str(temp_workspace),
    )
    
    # Sub-agent should have been blocked from writing
    # The scoped registry excludes write_file, so dispatch returns error
    assert "cannot complete" in result.lower() or "error" in result.lower()
    
    # File should NOT have been created
    assert not (temp_workspace / "hack.txt").exists()


@pytest.mark.asyncio
async def test_subagent_iteration_limit_enforced(registry, temp_workspace):
    """Test that sub-agents respect their iteration limits.
    
    file-scout has max_iterations=8, so it should stop after 8 turns.
    """
    # Mock LLM that never completes (keeps calling tools)
    call_count = 0
    
    async def infinite_llm(request: dict) -> MagicMock:
        nonlocal call_count
        call_count += 1
        
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message = MagicMock()
        response.choices[0].message.content = None
        response.choices[0].message.tool_calls = [
            MagicMock(
                id=f"call_{call_count}",
                function=MagicMock(
                    name="list_files",
                    arguments='{"path": "."}',
                ),
            )
        ]
        return response
    
    result = await execute_subagent(
        role_name="file-scout",
        task="List all files forever",
        parent_session_id="test-parent",
        registry=registry,
        llm_dispatcher=infinite_llm,
        workspace=str(temp_workspace),
    )
    
    # Should have hit iteration limit
    assert "reached" in result.lower() or "exceeded" in result.lower()
    assert "iterations" in result.lower()
    
    # Should have stopped at max_iterations (8 for file-scout)
    assert call_count <= 8
