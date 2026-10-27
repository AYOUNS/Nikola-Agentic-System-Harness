"""Tests for MCP slash command."""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.cli import mcp_failure_notice
from agent.cli.commands.builtin import mcp_command
from agent.cli.session import Session
from agent.cli.ui import ConsoleUI
from agent.mcp.manager import MCPManager
from agent.tools import ToolRegistry


@pytest.mark.asyncio
async def test_mcp_command_with_servers():
    """Test /mcp command shows connected servers and tools."""
    # Create temporary workspace with MCP config
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        # tests/unit/cli/ -> tests/, where the fixtures package lives.
        test_server_path = Path(__file__).parents[2] / "fixtures" / "simple_mcp_server.py"
        
        mcp_config = {
            "mcpServers": {
                "test": {
                    "command": "python",
                    "args": [str(test_server_path)],
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        # Setup
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        ui = Mock(spec=ConsoleUI)
        
        try:
            await manager.connect_all(registry)
            
            session = Session(
                messages=[],
                mode="default",
                model="test-model",
                tools=registry,
                ui=ui,
                mcp_manager=manager,
            )
            
            # Call /mcp command
            mcp_command(session, "")
            
            # One grouped overview, with the tools nested under their server
            ui.mcp_overview.assert_called_once()
            servers = list(ui.mcp_overview.call_args[0][0])
            assert len(servers) == 1

            name, state, tools, error = servers[0]
            assert name == "test"
            assert state == "connected"
            assert error is None  # No error for successful connection

            summaries = dict(tools)
            assert sorted(summaries) == ["add", "echo"]

            # Descriptions are reduced to the docstring summary, without the Args: block.
            assert summaries["echo"] == "Echo back the input message."
            assert "Args:" not in summaries["add"]
            
        finally:
            await manager.disconnect_all()


def test_mcp_command_no_integration():
    """Test /mcp command when no MCP integration exists."""
    registry = ToolRegistry()
    ui = Mock(spec=ConsoleUI)
    
    session = Session(
        messages=[],
        mode="default",
        model="test-model",
        tools=registry,
        ui=ui,
        mcp_manager=None,  # No MCP integration
    )
    
    mcp_command(session, "")
    
    # Should show info message
    ui.info.assert_called_once_with("No MCP integration configured.")


def test_mcp_command_no_servers():
    """Test /mcp command when MCP is integrated but no servers configured."""
    with tempfile.TemporaryDirectory() as workspace:
        manager = MCPManager(workspace)  # No nikola/mcp.json
        registry = ToolRegistry()
        ui = Mock(spec=ConsoleUI)
        
        session = Session(
            messages=[],
            mode="default",
            model="test-model",
            tools=registry,
            ui=ui,
            mcp_manager=manager,
        )
        
        mcp_command(session, "")
        
        # Should show info about missing config
        ui.info.assert_called_once()
        assert "not found or empty" in ui.info.call_args[0][0]


@pytest.mark.asyncio
async def test_mcp_command_shows_failed_server():
    """Test /mcp command shows failed servers with error messages."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        # Config with bogus command that will fail
        mcp_config = {
            "mcpServers": {
                "broken-server": {
                    "command": "/nonexistent/path/to/binary",
                    "args": [],
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        # Setup
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        ui = Mock(spec=ConsoleUI)
        
        try:
            # connect_all should not raise even though server fails
            await manager.connect_all(registry)
            
            session = Session(
                messages=[],
                mode="default",
                model="test-model",
                tools=registry,
                ui=ui,
                mcp_manager=manager,
            )
            
            # Call /mcp command
            mcp_command(session, "")
            
            # Should show the failed server
            ui.mcp_overview.assert_called_once()
            servers = list(ui.mcp_overview.call_args[0][0])
            assert len(servers) == 1
            
            name, state, tools, error = servers[0]
            assert name == "broken-server"
            assert state == "failed"
            assert len(list(tools)) == 0  # No tools from failed server
            assert error is not None  # Should have error message
            assert "FileNotFoundError" in error or "OSError" in error  # Error type in message
            
        finally:
            await manager.disconnect_all()


def test_mcp_failure_notice_names_the_reason_for_a_single_server():
    """One failure is usually the whole story, so its reason goes in the box."""
    what, todo = mcp_failure_notice({"github": "FileNotFoundError: npx"})

    assert what == "MCP server 'github' did not connect (FileNotFoundError: npx)"
    assert "/mcp" in todo


def test_mcp_failure_notice_counts_several_rather_than_listing_reasons():
    """Several reasons would overrun the box and bury the one that matters."""
    what, _ = mcp_failure_notice(
        {"weather": "TimeoutError: timed out", "github": "FileNotFoundError: npx"}
    )

    assert what == "2 MCP servers did not connect: github, weather"
    assert "TimeoutError" not in what


def test_mcp_failure_notice_truncates_a_runaway_reason():
    """A stack-trace-sized reason is cut, so the box stays a box."""
    what, _ = mcp_failure_notice({"chatty": "RuntimeError: " + "x" * 500})

    assert len(what) < 200
    assert what.endswith("…)")


def test_mcp_failure_notice_strips_the_notice_separator():
    """A reason containing "|" would otherwise be read as the what/todo split."""
    what, _ = mcp_failure_notice({"pipes": "ValueError: got a | in the message"})

    assert "|" not in what
