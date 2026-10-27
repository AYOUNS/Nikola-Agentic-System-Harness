"""Unit tests for sub-agent orchestration: make_delegate_tool and make_scoped_registry.

Tests that:
- make_scoped_registry filters to only allowed tools
- make_delegate_tool produces a correctly-shaped tool dict
- execute_subagent short-circuits on unknown role
"""

from __future__ import annotations

import pytest

from agent.tools import ToolRegistry, make_scoped_registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy_registry(*tool_names: str) -> ToolRegistry:
    """Build a ToolRegistry populated with no-op tools for each name."""
    reg = ToolRegistry()
    for name in tool_names:
        schema = {
            "type": "function",
            "function": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}},
        }
        reg.register(name=name, fn=lambda **_: "ok", schema=schema)
    return reg


# ---------------------------------------------------------------------------
# make_scoped_registry
# ---------------------------------------------------------------------------

class TestMakeScopedRegistry:
    """Tests for make_scoped_registry helper."""

    def test_empty_allowlist_produces_empty_registry(self):
        parent = _dummy_registry("read_file", "write_file", "list_files")
        scoped = make_scoped_registry(parent, [])
        assert scoped.get_schemas() == []

    def test_allowlist_filters_correctly(self):
        parent = _dummy_registry("read_file", "write_file", "list_files")
        scoped = make_scoped_registry(parent, ["read_file", "list_files"])
        names = {s["function"]["name"] for s in scoped.get_schemas()}
        assert names == {"read_file", "list_files"}

    def test_tool_not_in_parent_is_silently_skipped(self):
        """Tools named in allowlist but absent from parent don't raise."""
        parent = _dummy_registry("read_file")
        scoped = make_scoped_registry(parent, ["read_file", "nonexistent_tool"])
        names = {s["function"]["name"] for s in scoped.get_schemas()}
        assert names == {"read_file"}

    def test_accepts_set_allowlist(self):
        """make_scoped_registry accepts a set (as PermissionPolicy.allowlist is)."""
        parent = _dummy_registry("read_file", "write_file")
        scoped = make_scoped_registry(parent, {"read_file"})
        names = {s["function"]["name"] for s in scoped.get_schemas()}
        assert names == {"read_file"}

    def test_full_allowlist_preserves_all_tools(self):
        parent = _dummy_registry("read_file", "write_file", "list_files")
        scoped = make_scoped_registry(parent, ["read_file", "write_file", "list_files"])
        assert len(scoped.get_schemas()) == 3

    @pytest.mark.asyncio
    async def test_scoped_registry_can_dispatch_allowed_tool(self):
        """Dispatching a tool that is in the scoped registry works."""
        parent = _dummy_registry("read_file")
        scoped = make_scoped_registry(parent, ["read_file"])
        result = await scoped.dispatch("read_file", {})
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_scoped_registry_dispatch_unknown_returns_error(self):
        """Dispatching a tool not in the scoped registry returns an error string."""
        parent = _dummy_registry("read_file", "write_file")
        scoped = make_scoped_registry(parent, ["read_file"])  # write_file excluded
        result = await scoped.dispatch("write_file", {})
        assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# make_delegate_tool
# ---------------------------------------------------------------------------

class TestMakeDelegateTool:
    """Tests for make_delegate_tool factory."""

    def _make_delegate(self):
        from agent.subagents.orchestrator import make_delegate_tool
        parent = _dummy_registry("read_file", "list_files")
        return make_delegate_tool(
            parent_session_id="test-session",
            registry=parent,
            llm_dispatcher=None,  # not exercised in schema tests
            workspace="/workspace",
        )

    def test_returns_dict_with_required_keys(self):
        tool = self._make_delegate()
        assert "name" in tool
        assert "fn" in tool
        assert "schema" in tool

    def test_tool_name_is_delegate(self):
        tool = self._make_delegate()
        assert tool["name"] == "delegate"

    def test_schema_function_name_matches(self):
        tool = self._make_delegate()
        assert tool["schema"]["function"]["name"] == "delegate"

    def test_schema_has_role_and_task_parameters(self):
        tool = self._make_delegate()
        params = tool["schema"]["function"]["parameters"]["properties"]
        assert "role" in params
        assert "task" in params

    def test_schema_role_is_enum(self):
        """role parameter is an enum of known role names."""
        tool = self._make_delegate()
        role_param = tool["schema"]["function"]["parameters"]["properties"]["role"]
        assert "enum" in role_param
        assert "file-scout" in role_param["enum"]
        assert "scheduler" in role_param["enum"]

    def test_fn_is_callable(self):
        tool = self._make_delegate()
        assert callable(tool["fn"])


# ---------------------------------------------------------------------------
# execute_subagent error paths (no LLM call needed)
# ---------------------------------------------------------------------------

class TestExecuteSubagentErrors:
    """Test execute_subagent error handling without a real LLM."""

    @pytest.mark.asyncio
    async def test_unknown_role_returns_error_string(self):
        from agent.subagents.orchestrator import execute_subagent

        parent = _dummy_registry("read_file")
        result = await execute_subagent(
            role_name="nonexistent-role",
            task="do something",
            parent_session_id="test-session",
            registry=parent,
            llm_dispatcher=None,
            workspace="/workspace",
        )
        assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# make_delegate_parallel_tool
# ---------------------------------------------------------------------------

class TestMakeDelegateParallelTool:
    """Tests for make_delegate_parallel_tool factory (parallel delegation)."""

    def _make_delegate_parallel(self):
        from agent.subagents.orchestrator import make_delegate_parallel_tool
        parent = _dummy_registry("read_file", "list_files")
        return make_delegate_parallel_tool(
            parent_session_id="test-session",
            registry=parent,
            llm_dispatcher=None,
            workspace="/workspace",
        )

    def test_returns_dict_with_name_fn_schema(self):
        tool = self._make_delegate_parallel()
        assert "name" in tool
        assert "fn" in tool
        assert "schema" in tool

    def test_name_is_delegate_parallel(self):
        tool = self._make_delegate_parallel()
        assert tool["name"] == "delegate_parallel"

    def test_schema_is_openai_tool_param_format(self):
        tool = self._make_delegate_parallel()
        schema = tool["schema"]
        assert schema["type"] == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "delegate_parallel"

    def test_schema_includes_tasks_array_parameter(self):
        tool = self._make_delegate_parallel()
        params = tool["schema"]["function"]["parameters"]["properties"]
        assert "tasks" in params
        assert params["tasks"]["type"] == "array"

    def test_tasks_array_items_have_role_and_task_fields(self):
        tool = self._make_delegate_parallel()
        items = tool["schema"]["function"]["parameters"]["properties"]["tasks"]["items"]
        assert items["type"] == "object"
        assert "role" in items["properties"]
        assert "task" in items["properties"]

    def test_tasks_array_has_min_and_max_items(self):
        tool = self._make_delegate_parallel()
        tasks_param = tool["schema"]["function"]["parameters"]["properties"]["tasks"]
        assert tasks_param["minItems"] == 1
        assert tasks_param["maxItems"] == 5


# ---------------------------------------------------------------------------
# delegate_parallel validation
# ---------------------------------------------------------------------------

class TestDelegateParallelValidation:
    """Tests for delegate_parallel_fn input validation."""

    @pytest.mark.asyncio
    async def test_rejects_empty_tasks_array(self):
        """Empty tasks array returns error."""
        from agent.subagents.orchestrator import make_delegate_parallel_tool
        
        tool = make_delegate_parallel_tool(
            parent_session_id="test",
            registry=_dummy_registry(),
            llm_dispatcher=None,
            workspace="/workspace",
        )
        
        result = await tool["fn"](tasks=[])
        import json
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_rejects_invalid_role_name(self):
        """Task with non-existent role returns error."""
        from agent.subagents.orchestrator import make_delegate_parallel_tool
        
        tool = make_delegate_parallel_tool(
            parent_session_id="test",
            registry=_dummy_registry(),
            llm_dispatcher=None,
            workspace="/workspace",
        )
        
        result = await tool["fn"](tasks=[
            {"role": "nonexistent-role", "task": "Do something"}
        ])
        import json
        data = json.loads(result)
        assert "error" in data
