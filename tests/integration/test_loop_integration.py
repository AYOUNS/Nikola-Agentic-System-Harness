"""Integration tests for the ReAct loop wiring: MCP, skills, and rules.

Each test verifies that a component (MCP server, load_skill tool, workspace
rules) is actually wired into the registry or system prompt that the loop
receives — not just that the component exists in isolation.

Strategy: use ``tool_choice="required"`` (OpenAI spec) in a mock LLM response
to force the loop toward one specific tool, then assert the tool schema was
present in the request the LLM received, and that the tool actually executed.

No real LLM is called; no real MCP server is started for schema tests.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

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

from agent.loop import make_process_turn_fn_v2
from agent.tools import ToolRegistry, make_default_tool_registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool_call_completion(name: str, args: dict) -> ChatCompletion:
    """Build a mock ChatCompletion that calls one tool."""
    tc = ChatCompletionMessageToolCall(
        id=f"call_{name}",
        type="function",
        function=Function(name=name, arguments=json.dumps(args)),
    )
    msg = ChatCompletionMessage(role="assistant", content=None, tool_calls=[tc])
    return ChatCompletion(
        id="mock",
        choices=[Choice(finish_reason="tool_calls", index=0, message=msg)],
        created=0,
        model="mock",
        object="chat.completion",
    )


def _final_completion(content: str) -> ChatCompletion:
    """Build a mock ChatCompletion with a final text answer."""
    msg = ChatCompletionMessage(role="assistant", content=content, tool_calls=None)
    return ChatCompletion(
        id="mock",
        choices=[Choice(finish_reason="stop", index=0, message=msg)],
        created=0,
        model="mock",
        object="chat.completion",
    )


def _make_messages(user_text: str = "Do something.") -> list[ChatCompletionMessageParam]:
    return [
        ChatCompletionSystemMessageParam(role="system", content="You are a helpful agent."),
        ChatCompletionUserMessageParam(role="user", content=user_text),
    ]


# ---------------------------------------------------------------------------
# 1. Skill tool is registered in the loop's tool registry
# ---------------------------------------------------------------------------

def test_load_skill_tool_is_in_registry(tmp_path: Path) -> None:
    """make_default_tool_registry always registers load_skill.

    Verifies that when the registry is built (as _async_main does it),
    the 'load_skill' tool schema appears in get_schemas() — meaning the
    LLM will always see it as a callable tool in the request.
    """
    registry = make_default_tool_registry(workspace=str(tmp_path))

    tool_names = {s["function"]["name"] for s in registry.get_schemas()}
    assert "load_skill" in tool_names, (
        "load_skill must be registered so the LLM can load skills on demand"
    )


# ---------------------------------------------------------------------------
# 2. load_skill tool schema is sent to the LLM in the request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_skill_schema_present_in_llm_request(tmp_path: Path) -> None:
    """The LLM request's 'tools' list includes load_skill.

    Uses tool_choice="required" semantics by capturing the request dict
    the mock dispatcher receives and asserting load_skill is in the tools.
    """
    registry = make_default_tool_registry(workspace=str(tmp_path))

    captured_requests: list[dict] = []

    def mock_dispatch(req: dict) -> ChatCompletion:
        captured_requests.append(req)
        # First call: force-call load_skill (tool_choice="required" pattern)
        if len(captured_requests) == 1:
            return _tool_call_completion("load_skill", {"name": "filesystem"})
        # Second call: final answer
        return _final_completion("Done.")

    process_turn = make_process_turn_fn_v2(
        mock_dispatch, registry, {"loop": {"max_iterations": 5}}
    )
    await process_turn(_make_messages("Load the filesystem skill."))

    # The first request must have had load_skill in the tools list
    assert captured_requests, "Dispatcher was never called"
    first_request_tools = captured_requests[0].get("tools", [])
    tool_names_sent = {t["function"]["name"] for t in first_request_tools}
    assert "load_skill" in tool_names_sent, (
        f"load_skill not in tools sent to LLM; got: {sorted(tool_names_sent)}"
    )


# ---------------------------------------------------------------------------
# 3. load_skill execution returns skill body through the loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_skill_executes_through_loop(tmp_path: Path) -> None:
    """When the LLM calls load_skill, the observation is appended to history.

    Creates a real skill file in tmp_path/.nikola/skills/ and verifies the
    loop appends the skill content as a tool-result message.
    """
    # Create a workspace skill
    skills_dir = tmp_path / ".nikola" / "skills"
    skills_dir.mkdir(parents=True)
    skill_file = skills_dir / "test-skill.md"
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill\nversion: 1.0.0\n---\n\nDo things the test way.\n",
        encoding="utf-8",
    )

    # Reset the skills cache so the new skill is discovered
    import agent.skills.tool as _st
    _st._skills_cache = None

    registry = make_default_tool_registry(workspace=str(tmp_path))

    responses = [
        _tool_call_completion("load_skill", {"name": "test-skill"}),
        _final_completion("Skill loaded successfully."),
    ]
    idx = 0

    def mock_dispatch(req: dict) -> ChatCompletion:
        nonlocal idx
        r = responses[idx]
        idx += 1
        return r

    messages = _make_messages("Load the test-skill.")
    process_turn = make_process_turn_fn_v2(
        mock_dispatch, registry, {"loop": {"max_iterations": 5}}
    )
    answer = await process_turn(messages)

    # The final answer should come through
    assert "Skill loaded" in answer

    # The messages list should contain a tool result with the skill body
    tool_results = [m for m in messages if m.get("role") == "tool"]
    assert any("test way" in str(r.get("content", "")) for r in tool_results), (
        "Skill body should appear as a tool observation in the conversation"
    )


# ---------------------------------------------------------------------------
# 4. Rules from workspace are injected into the system prompt
# ---------------------------------------------------------------------------

def test_rules_appear_in_system_prompt(tmp_path: Path) -> None:
    """Rules written to workspace/.nikola/rules/ appear in the system message.

    The system_message() function (called at session start) loads rules and
    appends them. This test verifies the wiring without running the full loop.
    """
    from agent.cli.session import system_message

    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "01-style.md").write_text(
        "---\ndescription: Style guide\nenabled: true\n---\n\nAlways use snake_case.\n",
        encoding="utf-8",
    )

    msg = system_message(mode="default", workspace_root=str(tmp_path))
    content = msg["content"]

    assert "snake_case" in content, (
        "Rule content must be injected into the system prompt"
    )
    assert "User Rules" in content, (
        "Rules section header must appear in the system prompt"
    )


def test_disabled_rule_not_in_system_prompt(tmp_path: Path) -> None:
    """Rules with enabled: false must not appear in the system prompt."""
    from agent.cli.session import system_message

    rules_dir = tmp_path / ".nikola" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "disabled.md").write_text(
        "---\ndescription: Should be ignored\nenabled: false\n---\n\nNEVER_APPEAR.\n",
        encoding="utf-8",
    )

    msg = system_message(mode="default", workspace_root=str(tmp_path))
    assert "NEVER_APPEAR" not in msg["content"]


def test_no_rules_dir_produces_clean_system_prompt(tmp_path: Path) -> None:
    """Missing rules directory loads default rules without crashing."""
    from agent.cli.session import system_message

    msg = system_message(mode="default", workspace_root=str(tmp_path))
    # Should contain User Rules from package defaults (memory, auto-memory)
    assert "User Rules" in msg["content"]
    assert "auto-memory" in msg["content"]


# ---------------------------------------------------------------------------
# 5. MCP tools appear in the registry after connect_all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_tools_registered_in_registry() -> None:
    """After MCPManager.connect_all, MCP tools appear in the ToolRegistry.

    Uses a mock MCPClient so no real server is needed — this tests that
    the manager-to-registry wiring works, i.e. that tools discovered from
    a server actually get registered.
    """
    from agent.mcp.manager import MCPManager

    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()

        # Write a config pointing at a fake stdio server
        mcp_config = {
            "mcpServers": {
                "weather": {
                    "command": "python",
                    "args": ["-c", "import time; time.sleep(999)"],
                }
            }
        }
        (nikola_dir / "mcp.json").write_text(json.dumps(mcp_config))

        # Mock the StdioMCPClient so no process is spawned
        mock_client = AsyncMock()
        mock_client.connected = True
        mock_client.disconnected = False
        mock_client.startup_output = ""
        mock_client.list_tools = AsyncMock(return_value=[
            {
                "name": "get_forecast",
                "description": "Get weather forecast for a city",
                "inputSchema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ])

        def _translate(tool: dict) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": f"mcp_weather__{tool['name']}",
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }

        mock_client.translate_tool_schema = _translate

        from unittest.mock import patch
        with patch("agent.mcp.manager.StdioMCPClient", return_value=mock_client):
            manager = MCPManager(str(workspace_path))
            registry = ToolRegistry()
            await manager.connect_all(registry)

        tool_names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "mcp_weather__get_forecast" in tool_names, (
            "MCP tool must be registered in ToolRegistry after connect_all"
        )


# ---------------------------------------------------------------------------
# 6. MCP tool schema is sent to the LLM and executes through the loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mcp_tool_executes_through_loop() -> None:
    """When the LLM calls an MCP tool, the registry dispatches it and returns the result.

    Registers a fake MCP tool directly in the registry (bypassing the manager)
    and verifies the loop appends the tool observation to the conversation.
    """
    registry = ToolRegistry()

    # Register a fake MCP tool directly (simulates what connect_all does)
    async def get_forecast(city: str) -> str:
        return f"Sunny in {city}, 22°C"

    registry.register(
        name="mcp_weather__get_forecast",
        fn=get_forecast,
        schema={
            "type": "function",
            "function": {
                "name": "mcp_weather__get_forecast",
                "description": "Get weather forecast",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        },
    )

    captured: list[dict] = []

    def mock_dispatch(req: dict) -> ChatCompletion:
        captured.append(req)
        if len(captured) == 1:
            return _tool_call_completion("mcp_weather__get_forecast", {"city": "Berlin"})
        return _final_completion("The weather in Berlin is sunny.")

    messages = _make_messages("What's the weather in Berlin?")
    process_turn = make_process_turn_fn_v2(
        mock_dispatch, registry, {"loop": {"max_iterations": 5}}
    )
    answer = await process_turn(messages)

    # Schema was sent to the LLM
    first_tools = {t["function"]["name"] for t in captured[0].get("tools", [])}
    assert "mcp_weather__get_forecast" in first_tools

    # Tool result was appended
    tool_results = [m for m in messages if m.get("role") == "tool"]
    assert any("Berlin" in str(r.get("content", "")) for r in tool_results)

    # Final answer contains result
    assert "Berlin" in answer
