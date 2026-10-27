"""Streamable HTTP transport implementation for MCP clients.

Uses the official MCP Python SDK to communicate with MCP servers via HTTP/SSE.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from agent.mcp.client import MCPClient

logger = logging.getLogger(__name__)


class HttpMCPClient(MCPClient):
    """MCP client using streamable HTTP transport (SSE over HTTP/HTTPS).
    
    Lifecycle:
    1. connect() creates httpx.AsyncClient and establishes HTTP connection
    2. list_tools() discovers available tools via HTTP
    3. call_tool() executes tools via JSON-RPC over HTTP
    4. disconnect() closes HTTP connection and client
    
    Failure handling:
    - Connection failures during connect() are raised to caller
    - Network errors during operation mark client as disconnected
    - Subsequent tool calls return error strings, not exceptions
    
    Transport details:
    - Uses MCP SDK's streamable_http_client (SSE-based)
    - Manages httpx.AsyncClient lifecycle explicitly (SDK doesn't close it)
    - Headers and timeouts configured on httpx.AsyncClient, not transport
    - Credentials passed via headers, never logged
    """

    def __init__(self, server_name: str, config: dict[str, Any]):
        """Initialize HTTP MCP client.
        
        config must contain:
          url: str - HTTP/HTTPS endpoint (e.g. "https://example.com/mcp")
          headers: dict[str, str] - HTTP headers including auth (optional)
        """
        super().__init__(server_name, config)
        self.client: Client | None = None
        self._client_context: Any = None
        self._http_client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """Connect to MCP server via streamable HTTP and initialize session."""
        if self.connected:
            return

        url = self.config.get("url")
        headers = self.config.get("headers", {})

        if not url:
            raise ValueError(f"MCP server '{self.server_name}' missing 'url' in config")

        # Sanitize URL for logging (remove query params that might contain tokens)
        log_url = url.split("?")[0] if "?" in url else url
        logger.info(f"Connecting to MCP server '{self.server_name}': {log_url}")

        try:
            # Create httpx.AsyncClient with headers and timeouts
            # Per plan: timeout=30s default, connect=10s, read=300s (5min for long operations)
            self._http_client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(
                    timeout=30.0,      # Default timeout
                    connect=10.0,      # Connection timeout
                    read=300.0,        # Read timeout (long for streaming)
                ),
                follow_redirects=True,
            )

            # Create client with streamable HTTP transport context manager
            # The Client context manager handles connection lifecycle
            # Note: We pass our httpx.AsyncClient to the transport
            self._client_context = Client(
                streamable_http_client(url, http_client=self._http_client)
            )
            self.client = await self._client_context.__aenter__()

            self.connected = True
            logger.info(f"Successfully connected to MCP server '{self.server_name}'")

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error(f"Failed to connect to MCP server '{self.server_name}': {e}")
            self.disconnected = True
            # Clean up httpx client if it was created
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None
            raise

        except Exception as e:
            # Extract meaningful error from ExceptionGroup if present
            display_error = e
            if type(e).__name__ == "ExceptionGroup" and hasattr(e, "exceptions") and e.exceptions:
                display_error = e.exceptions[0]
            
            logger.error(f"Failed to connect to MCP server '{self.server_name}': {display_error}")
            self.disconnected = True
            # Clean up httpx client if it was created
            if self._http_client:
                await self._http_client.aclose()
                self._http_client = None
            raise

    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server and close HTTP client."""
        if not self.connected:
            return

        logger.info(f"Disconnecting from MCP server '{self.server_name}'")

        try:
            # Exit the client context manager first - this handles transport cleanup
            if self._client_context is not None:
                await self._client_context.__aexit__(None, None, None)
                self._client_context = None
                self.client = None

        except Exception as e:
            logger.warning(f"Error during disconnect from '{self.server_name}': {e}")
        finally:
            # Then close the httpx.AsyncClient we created
            # This is critical - SDK does not close clients it didn't create
            if self._http_client is not None:
                try:
                    await self._http_client.aclose()
                except Exception as e:
                    logger.warning(f"Error closing HTTP client for '{self.server_name}': {e}")
                finally:
                    self._http_client = None
            
            self.connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Query server for available tools via HTTP.
        
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

        except (httpx.HTTPError, httpx.ConnectError, httpx.ReadTimeout) as e:
            # Network/HTTP failure - mark as disconnected
            logger.error(f"Failed to list tools from '{self.server_name}': {e}")
            self.disconnected = True
            self.connected = False
            raise

        except Exception as e:
            logger.error(f"Failed to list tools from '{self.server_name}': {e}")
            # Mark as disconnected if communication failed
            self.disconnected = True
            self.connected = False
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute tool call via MCP protocol over HTTP.
        
        Handles errors per design Question 10:
        - Network/connection errors mark client as disconnected
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
            # Call tool via MCP protocol over HTTP
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

        except (httpx.HTTPError, httpx.ConnectError, httpx.ReadTimeout, ConnectionError) as e:
            # Network/connection failure - mark as permanently disconnected
            logger.error(f"Connection lost to MCP server '{self.server_name}': {e}")
            self.disconnected = True
            self.connected = False
            return f"Error: MCP server '{self.server_name}' is not responding"

        except Exception as e:
            # Other errors - tool-level failure, not connection failure
            logger.error(f"Tool call '{name}' failed on '{self.server_name}': {e}")
            return f"Error: {type(e).__name__}: {e}"
