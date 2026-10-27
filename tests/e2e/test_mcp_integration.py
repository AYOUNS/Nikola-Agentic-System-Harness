"""E2E tests for MCP integration.

Tests the full MCP integration flow:
- Loading .nikola/mcp.json configuration
- Connecting to stdio MCP servers
- Discovering and registering tools
- Executing tool calls through the registry
- Graceful disconnect
"""

import json
import tempfile
from pathlib import Path

import pytest

from agent.mcp.manager import MCPManager
from agent.tools import ToolRegistry


@pytest.mark.asyncio
async def test_mcp_stdio_integration():
    """Test full MCP integration with simple test server."""
    # Create temporary workspace with .nikola/mcp.json
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        # Get absolute path to test server
        test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
        
        # Write MCP config
        mcp_config = {
            "mcpServers": {
                "test": {
                    "command": "python",
                    "args": [str(test_server_path)],
                    "env": {},
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        # Create manager and registry
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        try:
            # Connect to MCP servers
            await manager.connect_all(registry)
            
            # Verify server connected
            assert len(manager.clients) == 1
            assert "test" in manager.clients
            assert manager.clients["test"].connected
            
            # Verify tools were registered
            schemas = registry.get_schemas()
            tool_names = [s["function"]["name"] for s in schemas]
            
            assert "mcp_test__echo" in tool_names
            assert "mcp_test__add" in tool_names
            
            # Test echo tool
            echo_result = await registry.dispatch("mcp_test__echo", {"message": "Hello MCP!"})
            assert "Echo: Hello MCP!" in echo_result
            assert not echo_result.startswith("Error:")
            
            # Test add tool
            add_result = await registry.dispatch("mcp_test__add", {"a": 5, "b": 3})
            assert "Result: 8" in add_result
            assert not add_result.startswith("Error:")
            
        finally:
            # Cleanup
            await manager.disconnect_all()
            assert len(manager.clients) == 0


@pytest.mark.asyncio
async def test_mcp_config_missing():
    """Test graceful handling when nikola/mcp.json doesn't exist."""
    with tempfile.TemporaryDirectory() as workspace:
        manager = MCPManager(workspace)
        registry = ToolRegistry()
        
        # Should not raise, just log and continue
        await manager.connect_all(registry)
        
        assert len(manager.clients) == 0
        assert len(registry.get_schemas()) == 0


@pytest.mark.asyncio
async def test_mcp_invalid_json():
    """Test graceful handling of invalid JSON in .nikola/mcp.json."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        # Write invalid JSON
        config_path = nikola_dir / "mcp.json"
        config_path.write_text("{invalid json")
        
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        # Should not raise, just log error and continue
        await manager.connect_all(registry)
        
        assert len(manager.clients) == 0


@pytest.mark.asyncio
async def test_mcp_env_var_substitution():
    """Test environment variable substitution in server config."""
    import os
    
    # Set test env var
    os.environ["TEST_MCP_VAR"] = "test_value"
    
    try:
        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            nikola_dir = workspace_path / ".nikola"
            nikola_dir.mkdir()
            
            test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
            
            # Config with env var substitution
            mcp_config = {
                "mcpServers": {
                    "test": {
                        "command": "python",
                        "args": [str(test_server_path)],
                        "env": {
                            "MY_VAR": "${TEST_MCP_VAR}",
                            "LITERAL": "not_substituted",
                        },
                    }
                }
            }
            
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(mcp_config, indent=2))
            
            manager = MCPManager(str(workspace_path))
            registry = ToolRegistry()
            
            try:
                await manager.connect_all(registry)
                
                # Verify connection succeeded
                assert len(manager.clients) == 1
                
                # Verify env was processed
                client = manager.clients["test"]
                assert client.config["env"]["MY_VAR"] == "test_value"
                assert client.config["env"]["LITERAL"] == "not_substituted"
                
            finally:
                await manager.disconnect_all()
    
    finally:
        # Cleanup env var
        os.environ.pop("TEST_MCP_VAR", None)


@pytest.mark.asyncio
async def test_mcp_tool_error_handling():
    """Test error handling when tool execution fails."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
        
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
        
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        try:
            await manager.connect_all(registry)
            
            # Call with missing required argument
            result = await registry.dispatch("mcp_test__echo", {})
            
            # Should return error string, not raise
            assert result.startswith("Error:")
            
        finally:
            await manager.disconnect_all()
"""E2E tests for MCP integration with mixed transports.

Tests the full MCP integration flow with both stdio and HTTP transports:
- Loading nikola/mcp.json configuration with multiple transport types
- Connecting to both stdio and HTTP servers
- Graceful failure handling when servers are unavailable
- Transport factory dispatching
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_mixed_stdio_and_http_servers():
    """Test manager can handle both stdio and HTTP servers simultaneously."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        # Get path to test stdio server
        test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
        
        # Config with both stdio and HTTP (HTTP will be mocked)
        mcp_config = {
            "mcpServers": {
                "local-stdio": {
                    "type": "stdio",
                    "command": "python",
                    "args": [str(test_server_path)],
                    "env": {}
                },
                "remote-http": {
                    "type": "http",
                    "url": "https://mocked-api.example.com/mcp",
                    "headers": {}
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        # Mock HTTP client to avoid actual network calls
        mock_http_client_instance = AsyncMock()
        mock_http_client_instance.connected = True
        mock_http_client_instance.disconnected = False
        
        # Mock list_tools for HTTP client
        mock_http_client_instance.list_tools = AsyncMock(return_value=[
            {
                "name": "http_tool",
                "description": "A tool from HTTP server",
                "inputSchema": {"type": "object", "properties": {}}
            }
        ])
        
        # Mock translate_tool_schema for HTTP client
        def mock_translate(tool):
            return {
                "type": "function",
                "function": {
                    "name": f"mcp_remote-http__{tool['name']}",
                    "description": tool["description"],
                    "parameters": tool["inputSchema"]
                }
            }
        mock_http_client_instance.translate_tool_schema = mock_translate
        
        with patch("agent.mcp.manager.HttpMCPClient") as MockHttpClient:
            MockHttpClient.return_value = mock_http_client_instance
            
            try:
                await manager.connect_all(registry)
                
                # Should have both clients
                assert len(manager.clients) == 2
                assert "local-stdio" in manager.clients
                assert "remote-http" in manager.clients
                
                # Should have tools from both servers
                schemas = registry.get_schemas()
                tool_names = [s["function"]["name"] for s in schemas]
                
                # Stdio tools (from simple_mcp_server.py)
                assert "mcp_local-stdio__echo" in tool_names
                assert "mcp_local-stdio__add" in tool_names
                
                # HTTP tool (mocked)
                assert "mcp_remote-http__http_tool" in tool_names
                
                # Check status includes both
                status = manager.get_server_status()
                assert "local-stdio" in status
                assert "remote-http" in status
                assert status["local-stdio"]["connected"] is True
                assert status["remote-http"]["connected"] is True
                
            finally:
                await manager.disconnect_all()


@pytest.mark.asyncio
async def test_http_server_failure_isolated():
    """Test that HTTP server connection failure doesn't affect stdio server."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
        
        # Config with working stdio and failing HTTP
        mcp_config = {
            "mcpServers": {
                "working-stdio": {
                    "command": "python",
                    "args": [str(test_server_path)]
                },
                "broken-http": {
                    "type": "http",
                    "url": "https://unreachable.example.com/mcp"
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        # Mock HTTP client to fail on connect
        mock_http_client = AsyncMock()
        mock_http_client.connect = AsyncMock(side_effect=ConnectionError("Connection refused"))
        
        with patch("agent.mcp.manager.HttpMCPClient", return_value=mock_http_client):
            try:
                # Should not raise even though HTTP fails
                await manager.connect_all(registry)
                
                # Stdio should be connected
                assert "working-stdio" in manager.clients
                assert manager.clients["working-stdio"].connected is True
                
                # HTTP should be in failed list
                assert "broken-http" in manager._failed
                assert "ConnectionError" in manager._failed["broken-http"]
                
                # Status should show both
                status = manager.get_server_status()
                assert status["working-stdio"]["connected"] is True
                assert status["broken-http"]["connected"] is False
                assert status["broken-http"]["disconnected"] is True
                assert status["broken-http"]["error"] is not None
                
            finally:
                await manager.disconnect_all()


@pytest.mark.asyncio
async def test_transport_type_inference():
    """Test that manager correctly infers transport types from config."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
        
        # Config with implicit types (no explicit 'type' field)
        mcp_config = {
            "mcpServers": {
                "inferred-stdio": {
                    "command": "python",
                    "args": [str(test_server_path)]
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        try:
            await manager.connect_all(registry)
            
            # Should correctly infer stdio and create StdioMCPClient
            assert "inferred-stdio" in manager.clients
            from agent.mcp.stdio_client import StdioMCPClient
            assert isinstance(manager.clients["inferred-stdio"], StdioMCPClient)
            
        finally:
            await manager.disconnect_all()


@pytest.mark.asyncio
async def test_disabled_servers_not_loaded():
    """Test that servers with disabled=true are not loaded."""
    with tempfile.TemporaryDirectory() as workspace:
        workspace_path = Path(workspace)
        nikola_dir = workspace_path / ".nikola"
        nikola_dir.mkdir()
        
        test_server_path = Path(__file__).parent.parent / "fixtures" / "simple_mcp_server.py"
        
        mcp_config = {
            "mcpServers": {
                "enabled-server": {
                    "command": "python",
                    "args": [str(test_server_path)],
                    "disabled": False
                },
                "disabled-server": {
                    "command": "python",
                    "args": [str(test_server_path)],
                    "disabled": True
                }
            }
        }
        
        config_path = nikola_dir / "mcp.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))
        
        manager = MCPManager(str(workspace_path))
        registry = ToolRegistry()
        
        try:
            await manager.connect_all(registry)
            
            # Only enabled server should be loaded
            assert "enabled-server" in manager.clients
            assert "disabled-server" not in manager.clients
            assert "disabled-server" not in manager._failed
            
            # Status should only include enabled server
            status = manager.get_server_status()
            assert "enabled-server" in status
            assert "disabled-server" not in status
            
        finally:
            await manager.disconnect_all()


