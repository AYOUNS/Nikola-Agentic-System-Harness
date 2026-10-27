"""MCP Manager - orchestrates MCP server lifecycle and tool registration.

Responsibilities:
- Load server configs from nikola/mcp.json
- Create and connect MCP clients
- Discover tools from all servers
- Register tools in ToolRegistry with proper namespacing
- Handle graceful cleanup on session end
"""

from __future__ import annotations

import logging
from typing import Any

from agent.hooks.types import MCPServerEvent, get_hook_registry
from agent.mcp.client import MCPClient
from agent.mcp.config import load_mcp_config
from agent.mcp.http_client import HttpMCPClient
from agent.mcp.stdio_client import StdioMCPClient
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MCPManager:
    """Manages lifecycle of all configured MCP servers.
    
    Per design decisions:
    - Owns MCPClient instances but not subprocess details
    - Connects servers eagerly at Session start (sequential)
    - Discovers tools and registers them in ToolRegistry
    - Tool naming: mcp_<server>__<tool> (e.g. mcp_weather__get_forecast)
    - Connection failures: log warning, skip that server, continue
    
    Usage:
        manager = MCPManager(workspace_root="/workspace")
        await manager.connect_all(tool_registry)
        # ... session runs ...
        await manager.disconnect_all()
    """

    def __init__(self, workspace_root: str):
        """Initialize MCP manager.

        workspace_root: Path to workspace containing .nikola/mcp.json
        """
        self.workspace_root = workspace_root
        self.clients: dict[str, MCPClient] = {}
        self._tool_to_client: dict[str, MCPClient] = {}  # Maps namespaced tool name to client
        self._failed: dict[str, str] = {}  # Maps failed server name to failure reason

    async def connect_all(self, registry: ToolRegistry) -> None:
        """Connect to all configured MCP servers and register their tools.
        
        Per design Question 6 (Option B):
        - Eager sequential connection at Session start
        - Connection failures are logged but don't stop other servers
        - Failed servers are skipped, not added to self.clients
        
        registry: ToolRegistry where discovered tools will be registered
        """
        logger.info(f"Loading MCP configuration from {self.workspace_root}/.nikola/mcp.json")
        
        server_configs = load_mcp_config(self.workspace_root)
        
        if not server_configs:
            logger.info("No MCP servers configured")
            return
        
        logger.info(f"Found {len(server_configs)} MCP server(s) configured")

        for server_name, config in server_configs.items():
            await self._connect_server(server_name, config, registry)

    @property
    def failures(self) -> dict[str, str]:
        """Server name to failure reason, for every server that did not come up.

        Empty when all configured servers connected. _connect_server swallows
        these deliberately, so other servers keep loading; this is how a caller
        learns it happened and can say so on screen.
        """
        return dict(self._failed)

    @property
    def startup_output(self) -> dict[str, str]:
        """Server name to whatever it wrote to stderr while starting up.

        Only servers that wrote something appear. Their stderr is captured
        rather than inherited, so this is the only way the text reaches the
        screen — and it reaches it inside a box, not above the banner.
        """
        return {
            name: client.startup_output
            for name, client in self.clients.items()
            if getattr(client, "startup_output", "")
        }

    def _make_client(self, server_name: str, config: dict[str, Any]) -> MCPClient:
        """Factory method to create appropriate MCP client based on transport type.
        
        Dispatches on config['type']:
        - 'stdio': creates StdioMCPClient
        - 'http': creates HttpMCPClient
        
        server_name: Server identifier from mcp.json
        config: Server configuration dict with 'type' field
        
        Returns:
            Appropriate MCPClient subclass instance
            
        Raises:
            ValueError: if transport type is unknown
        """
        transport_type = config.get("type", "stdio")  # Default to stdio for backward compat
        
        if transport_type == "stdio":
            return StdioMCPClient(server_name, config)
        elif transport_type == "http":
            return HttpMCPClient(server_name, config)
        else:
            raise ValueError(
                f"Unknown transport type '{transport_type}' for server '{server_name}'. "
                f"Supported types: 'stdio', 'http'"
            )

    async def _connect_server(
        self,
        server_name: str,
        config: dict[str, Any],
        registry: ToolRegistry,
    ) -> None:
        """Connect to a single MCP server and register its tools.
        
        Connection/discovery failures are logged but don't raise - this allows
        other servers to continue loading.
        """
        try:
            logger.info(f"Connecting to MCP server '{server_name}'...")
            
            # Create client using transport factory
            client = self._make_client(server_name, config)
            
            # Connect and initialize
            await client.connect()
            
            # Discover tools
            tools = await client.list_tools()
            logger.info(f"Discovered {len(tools)} tool(s) from '{server_name}'")
            
            # Register tools with namespace prefix
            for tool in tools:
                # Translate MCP schema to OpenAI format with mcp_<server>__<tool> naming
                openai_schema = client.translate_tool_schema(tool)
                tool_name = openai_schema["function"]["name"]
                
                # Create async wrapper for tool dispatch
                async def tool_fn(
                    _client=client,
                    _tool_name=tool["name"],
                    **kwargs: Any,
                ) -> str:
                    """Async wrapper for MCP tool call."""
                    return await _client.call_tool(_tool_name, kwargs)
                
                # Register in ToolRegistry
                registry.register(
                    name=tool_name,
                    fn=tool_fn,
                    schema=openai_schema,
                )
                
                # Track mapping for cleanup
                self._tool_to_client[tool_name] = client
                
                logger.debug(f"Registered MCP tool: {tool_name}")
            
            # Store client for later cleanup
            self.clients[server_name] = client

            get_hook_registry().emit_mcp_server(MCPServerEvent(
                server_name=server_name,
                event="connected",
                transport=client.transport,
                tools_discovered=len(tools),
            ))

            logger.info(f"Successfully initialized MCP server '{server_name}'")

        except Exception as e:
            # Track failed server with reason
            # Note: client already logged the connection failure at error level

            # Handle ExceptionGroup (Python 3.11+) - extract first sub-exception
            error_msg = self._extract_error_message(e)
            self._failed[server_name] = error_msg

            get_hook_registry().emit_mcp_server(MCPServerEvent(
                server_name=server_name,
                event="failed",
                transport=config.get("type", "unknown"),
                error=error_msg,
            ))

            logger.info(f"Continuing without '{server_name}' server")
    
    def _extract_error_message(self, e: Exception) -> str:
        """Extract meaningful error message from exception, handling ExceptionGroup.
        
        ExceptionGroup can be nested, so we recursively extract the first leaf exception.
        """
        # Check for ExceptionGroup (Python 3.11+)
        if type(e).__name__ == "ExceptionGroup" and hasattr(e, "exceptions") and e.exceptions:
            # Recursively extract from first sub-exception
            return self._extract_error_message(e.exceptions[0])
        
        # Regular exception
        error_str = str(e)
        if not error_str or error_str == "":
            error_str = "No error message provided"
        
        return f"{type(e).__name__}: {error_str}"

    async def disconnect_all(self) -> None:
        """Disconnect all connected MCP servers.
        
        Called during Session cleanup. Errors during disconnect are logged
        but don't raise - we want to attempt cleanup of all servers even
        if some fail.
        """
        if not self.clients:
            return
        
        logger.info(f"Disconnecting {len(self.clients)} MCP server(s)")
        
        for server_name, client in self.clients.items():
            try:
                await client.disconnect()
                logger.info(f"Disconnected from MCP server '{server_name}'")
                get_hook_registry().emit_mcp_server(MCPServerEvent(
                    server_name=server_name,
                    event="disconnected",
                    transport=client.transport,
                ))
            except Exception as e:
                logger.warning(f"Error disconnecting from '{server_name}': {e}")
        
        self.clients.clear()
        self._tool_to_client.clear()

    def get_server_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all MCP servers, including failed ones.
        
        Returns:
            Dict mapping server names to status info:
            {
              "server-name": {
                "connected": bool,
                "disconnected": bool,
                "tool_count": int,
                "error": str (optional, only for failed servers)
              }
            }
        """
        status = {}
        
        # Add successfully connected servers
        for server_name, client in self.clients.items():
            tool_count = sum(
                1 for tool_name in self._tool_to_client
                if self._tool_to_client[tool_name] == client
            )
            status[server_name] = {
                "connected": client.connected,
                "disconnected": client.disconnected,
                "tool_count": tool_count,
            }
        
        # Add failed servers
        for server_name, error_msg in self._failed.items():
            status[server_name] = {
                "connected": False,
                "disconnected": True,
                "tool_count": 0,
                "error": error_msg,
            }
        
        return status
