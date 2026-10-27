"""Stdio transport implementation for MCP clients.

Uses the official MCP Python SDK to communicate with MCP servers via stdio.
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from agent.mcp.client import MCPClient

logger = logging.getLogger(__name__)


class StdioMCPClient(MCPClient):
    """MCP client using stdio transport (subprocess with JSON-RPC over stdin/stdout).
    
    Lifecycle:
    1. connect() spawns subprocess and performs MCP initialization handshake
    2. list_tools() discovers available tools
    3. call_tool() executes tools via JSON-RPC
    4. disconnect() terminates subprocess gracefully (SIGTERM then SIGKILL)
    
    Failure handling:
    - Connection failures during connect() are raised to caller
    - Subprocess death during operation marks client as disconnected
    - Subsequent tool calls return error strings, not exceptions
    """

    def __init__(self, server_name: str, config: dict[str, Any]):
        """Initialize stdio MCP client.
        
        config must contain:
          command: str - executable command (e.g. "uvx", "python")
          args: list[str] - command arguments (e.g. ["mcp-server-weather"])
          env: dict[str, str] - environment variables (optional)
        """
        super().__init__(server_name, config)
        self.client: Client | None = None
        self._client_context: Any = None
        self._errlog: Any = None
        # What the server wrote to stderr while starting up. Captured rather
        # than inherited so a server's banner cannot land above our own.
        self.startup_output: str = ""

    async def connect(self) -> None:
        """Connect to MCP server via stdio and initialize session."""
        if self.connected:
            return

        command = self.config.get("command")
        args = self.config.get("args", [])
        env = self.config.get("env", {})

        if not command:
            raise ValueError(f"MCP server '{self.server_name}' missing 'command' in config")

        logger.info(f"Connecting to MCP server '{self.server_name}': {command} {' '.join(args)}")

        try:
            # Create server parameters
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env if env else None,
            )

            # errlog needs a real file descriptor — it is handed to the
            # subprocess as stderr — so a temp file, not an in-memory buffer.
            self._errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8")  # noqa: SIM115

            # Create client with stdio transport context manager
            # The Client context manager handles subprocess lifecycle
            self._client_context = Client(stdio_client(server_params, errlog=self._errlog))
            self.client = await self._client_context.__aenter__()

            self.startup_output = self._drain_errlog()
            if self.startup_output:
                logger.info(
                    f"MCP server '{self.server_name}' wrote to stderr on startup",
                    extra={"stderr": self.startup_output},
                )

            self.connected = True
            logger.info(f"Successfully connected to MCP server '{self.server_name}'")

        except Exception as e:
            # Whatever the server managed to say before dying is usually the
            # real reason, so keep it for the caller's failure notice.
            self.startup_output = self._drain_errlog()
            logger.error(f"Failed to connect to MCP server '{self.server_name}': {e}")
            self.disconnected = True
            raise

    def _drain_errlog(self) -> str:
        """Return what the server has written to stderr so far, or ""."""
        if self._errlog is None:
            return ""
        try:
            self._errlog.flush()
            self._errlog.seek(0)
            return self._errlog.read().strip()
        except (OSError, ValueError):
            return ""

    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server with SIGTERM/SIGKILL fallback."""
        if not self.connected:
            return

        logger.info(f"Disconnecting from MCP server '{self.server_name}'")

        try:
            # Exit the client context manager - this handles subprocess cleanup
            if self._client_context is not None:
                await self._client_context.__aexit__(None, None, None)
                self._client_context = None
                self.client = None

        except Exception as e:
            logger.warning(f"Error during disconnect from '{self.server_name}': {e}")
        finally:
            self.connected = False
            if self._errlog is not None:
                self._errlog.close()
                self._errlog = None

    async def list_tools(self) -> list[dict[str, Any]]:
        """Query server for available tools.
        
        Returns:
            List of MCP tool definitions with name, description, inputSchema
        """
        if self.disconnected:
            raise RuntimeError(f"MCP server '{self.server_name}' is disconnected")

        if not self.client:
            raise RuntimeError(f"MCP client '{self.server_name}' not connected")

        try:
            result = await self.client.list_tools()
            
            # Convert MCP Tool objects to dicts
            tools = []
            for tool in result.tools:
                tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.input_schema,  # MCP 2.x uses snake_case
                })
            
            return tools

        except Exception as e:
            logger.error(f"Failed to list tools from '{self.server_name}': {e}")
            # Mark as disconnected if communication failed
            self.disconnected = True
            self.connected = False
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute tool call via MCP protocol.
        
        Handles errors per design Question 10:
        - Connection errors mark client as disconnected
        - Tool errors return error strings
        - Non-text content returns error string
        """
        # Check if permanently disconnected
        if self.disconnected:
            return f"Error: MCP server '{self.server_name}' is not responding"

        if not self.client:
            self.disconnected = True
            return f"Error: MCP server '{self.server_name}' is not responding"

        try:
            # Call tool via MCP protocol
            result = await self.client.call_tool(name, arguments or {})

            # Check if tool returned an error
            if result.is_error:  # MCP 2.x uses snake_case
                # Extract error message from content
                error_parts = []
                for item in result.content:
                    if isinstance(item, TextContent):
                        error_parts.append(item.text)
                return f"Error: {' '.join(error_parts)}" if error_parts else "Error: Tool execution failed"

            # Extract text content (text only)
            text_parts = []
            for item in result.content:
                if isinstance(item, TextContent):
                    text_parts.append(item.text)
                else:
                    # Non-text content (image, resource) - return error per design
                    return f"Error: Tool '{name}' returned unsupported content type"

            if not text_parts:
                return "Error: Tool returned no text content"

            return "\n".join(text_parts)

        except (ConnectionError, BrokenPipeError, EOFError) as e:
            # Connection failure - mark as permanently disconnected
            logger.error(f"Connection lost to MCP server '{self.server_name}': {e}")
            self.disconnected = True
            self.connected = False
            return f"Error: MCP server '{self.server_name}' is not responding"

        except Exception as e:
            # Other errors - tool-level failure, not connection failure
            logger.error(f"Tool call '{name}' failed on '{self.server_name}': {e}")
            return f"Error: {type(e).__name__}: {e}"
