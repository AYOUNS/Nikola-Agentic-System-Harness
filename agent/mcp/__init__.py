"""MCP (Model Context Protocol) integration for the agent harness.

This module provides client implementations for connecting to MCP servers
and integrating their tools into the agent's ToolRegistry.

Supports:
- Stdio transport via StdioMCPClient
- HTTP transport via HttpMCPClient
- MCPManager for lifecycle orchestration
- Tool discovery and registration with naming pattern: mcp_<server>__<tool>
- Graceful failure handling
"""

from agent.mcp.manager import MCPManager

__all__ = ["MCPManager"]
