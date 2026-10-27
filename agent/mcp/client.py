"""MCP Client base class and protocol definition.

Defines the abstract interface that all MCP transport implementations must follow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from openai.types.chat import ChatCompletionToolParam


class MCPClient(ABC):
    """Base class for MCP clients across different transports.
    
    Responsibilities:
    - Connect to and disconnect from an MCP server
    - Discover available tools from the server
    - Execute tool calls via MCP protocol
    - Translate MCP schemas to OpenAI function-calling format
    - Handle connection failures gracefully
    
    Subclasses implement transport-specific connection logic (stdio, HTTP).
    """

    def __init__(self, server_name: str, config: dict[str, Any]):
        """Initialize the MCP client.
        
        server_name: Unique identifier for this server (from .nikola/mcp.json keys)
        config: Server configuration dict with transport-specific fields
        """
        self.server_name = server_name
        self.config = config
        self.transport = config.get("type", "unknown")
        self.connected = False
        self.disconnected = False  # Marks permanently failed connection

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the MCP server and perform initialization handshake.
        
        Raises:
            Exception on connection failure (caller should catch and handle)
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect from the MCP server.
        
        Should be called during Session cleanup. Implementations must handle
        the case where the server has already died.
        """

    @abstractmethod
    async def list_tools(self) -> list[dict[str, Any]]:
        """Query the server for available tools.
        
        Returns:
            List of tool definitions in MCP format (with inputSchema)
            
        Raises:
            Exception if server is disconnected or query fails
        """

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call on the MCP server.
        
        name: Tool name (without mcp_<server>__ prefix)
        arguments: Tool arguments dict
        
        Returns:
            Tool result as string (text content only for Week 2)
            
        Returns error string if:
        - Server is disconnected
        - Tool execution fails
        - Response contains non-text content (image/resource)
        """

    def translate_tool_schema(self, mcp_tool: dict[str, Any]) -> ChatCompletionToolParam:
        """Translate MCP tool schema to OpenAI function-calling format.
        
        MCP format:
        {
          "name": "tool_name",
          "description": "...",
          "inputSchema": {
            "type": "object",
            "properties": {...},
            "required": [...]
          }
        }
        
        OpenAI format:
        {
          "type": "function",
          "function": {
            "name": "mcp_<server>__tool_name",  
            "description": "...",
            "parameters": {
              "type": "object",
              "properties": {...},
              "required": [...]
            }
          }
        }
        """
        # Add namespace prefix: mcp_<server>__<tool>
        namespaced_name = f"mcp_{self.server_name}__{mcp_tool['name']}"
        
        return {
            "type": "function",
            "function": {
                "name": namespaced_name,
                "description": mcp_tool.get("description", ""),
                "parameters": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
            },
        }

    def strip_namespace(self, namespaced_name: str) -> str:
        """Remove mcp_<server>__ prefix from tool name.
        
        namespaced_name: Full tool name like "mcp_weather__get_forecast"
        
        Returns:
            Original tool name like "get_forecast"
        """
        prefix = f"mcp_{self.server_name}__"
        if namespaced_name.startswith(prefix):
            return namespaced_name[len(prefix):]
        return namespaced_name
