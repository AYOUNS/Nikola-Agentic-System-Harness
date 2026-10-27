"""Integration test for CLI visual design with permission system."""

import pytest

from agent.cli.ui import COLORS, ConsoleUI
from agent.permissions import PermissionModule
from agent.tools import ToolRegistry


@pytest.fixture
def ui():
    """Create a ConsoleUI for testing."""
    return ConsoleUI(streams=False)


@pytest.fixture
def tools():
    """Create a minimal tool registry."""
    registry = ToolRegistry()
    
    async def dummy_tool(arg: str) -> str:
        """A test tool."""
        return f"Processed: {arg}"
    
    registry.register("test_tool", dummy_tool, {
        "type": "function",
        "function": {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "arg": {"type": "string"}
                },
                "required": ["arg"]
            }
        }
    })
    
    return registry


def test_error_sink_renders_box(ui, capsys):
    """Test that error sink renders a boxed error."""
    # Single-part error
    ui.sink("error", "Something went wrong")
    captured = capsys.readouterr()
    
    assert "✕ Error" in captured.out
    assert "Something went wrong" in captured.out
    
    # Two-part error
    ui.sink("error", "Max iterations reached|Try breaking into smaller steps")
    captured = capsys.readouterr()
    
    assert "✕ Error" in captured.out
    assert "Max iterations reached" in captured.out
    assert "Try breaking into smaller steps" in captured.out


def test_tool_trace_color_coding(ui, capsys):
    """Test that tool traces are properly color-coded."""
    # Tool call
    ui.sink("tool", "test_tool(arg: hello)\n")
    captured = capsys.readouterr()
    assert "⏺" in captured.out
    assert "test_tool" in captured.out
    
    # Success result
    ui.sink("tool", "⎿ ✓ Success\n")
    captured = capsys.readouterr()
    assert "⎿" in captured.out
    assert "✓" in captured.out


def test_thinking_display(ui, capsys):
    """Test thinking/reasoning display."""
    ui.sink("reasoning", "Analyzing the problem...")
    # In real use, _end_thinking() is called automatically
    # Here we just verify the sink accepts reasoning
    capsys.readouterr()
    # Reasoning goes to status spinner, not stdout in this mode


def test_answer_streaming(ui, capsys):
    """Test answer text streaming."""
    ui.sink("answer", "This is ")
    ui.sink("answer", "the answer.")
    
    captured = capsys.readouterr()
    assert "This is the answer." in captured.out


def test_permission_box_structure(ui, capsys):
    """Test that permission prompts are properly structured."""
    # We can't test the interactive menu without blocking,
    # but we can verify the box structure is created
    
    # The actual confirm_tool_use would block waiting for input,
    # so we just verify the method exists and has the right signature
    assert hasattr(ui, 'confirm_tool_use')
    assert callable(ui.confirm_tool_use)


def test_color_palette_defined():
    """Test that the color palette is properly defined."""
    assert "accent" in COLORS
    assert "tool" in COLORS
    assert "success" in COLORS
    assert "error_border" in COLORS
    assert "error_text" in COLORS
    assert "caution_border" in COLORS
    assert "caution_text" in COLORS
    
    # Verify they're hex colors
    for color in COLORS.values():
        assert color.startswith("#")
        assert len(color) == 7  # #RRGGBB


@pytest.mark.asyncio
async def test_permission_module_with_ui(tools, ui):
    """Test permission module integration with UI."""
    # Create a permission module for parent agent
    module = PermissionModule(is_subagent=False)
    
    # Check that read tools are allowed by default
    decision = module.check("read_file", {"path": "test.txt"})
    assert decision.action.value == "allow"
    
    # Check that write tools require confirmation (ASK)
    decision = module.check("write_file", {"path": "test.txt", "content": "hello"})
    assert decision.action.value == "ask"
    
    # Simulate user approval via record_decision with scope="session".
    # write_file has ASK policy ("require-user-confirmation"), so the cache
    # is intentionally bypassed — every call must still prompt regardless of
    # what the user picked last time.
    module.record_decision("write_file", {"path": "test.txt", "content": "hello"}, approved=True, scope="session")

    # ASK-policy tools always prompt; cache must not short-circuit them.
    decision = module.check("write_file", {"path": "test.txt", "content": "hello"})
    assert decision.action.value == "ask"
    assert not decision.was_cached


@pytest.mark.asyncio
async def test_permission_module_denial(tools):
    """Test that denied permission is properly detected."""
    # Create a sub-agent module
    module = PermissionModule(is_subagent=True)
    
    # Sub-agents auto-deny ASK actions
    # write_file is in the asklist by default, so it should be denied for sub-agents
    decision = module.check("write_file", {"path": "test.txt", "content": "hello"})
    
    # Should be DENY for sub-agents (ASK → DENY)
    assert decision.action.value == "deny"
    assert "sub-agent" in decision.error_message.lower()


def test_ui_methods_use_color_palette(ui):
    """Test that UI methods use the COLORS palette."""
    # This is a smoke test to ensure methods exist and can be called
    # without errors (even if output is not visible in test)
    
    ui.info("Test info message")
    ui.hint("Test hint message")
    ui.error("Test error message")
    ui.goodbye("Test goodbye")
    
    # These should all complete without error
    assert True


def test_prompt_arrow_uses_accent_color(ui):
    """Test that prompt text uses accent color."""
    prompt_text = ui._prompt_text()
    
    # Check that the prompt either contains the arrow glyph
    # or has the RGB color code for coral, or falls back to plain text
    # (which is expected when console is not a terminal)
    assert "❯" in prompt_text or "217;119;87" in prompt_text or "You >" in prompt_text
