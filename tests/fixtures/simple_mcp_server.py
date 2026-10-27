#!/usr/bin/env python3
"""Simple test MCP server for E2E testing.

Exposes two simple tools:
- echo: Returns the input message
- add: Adds two numbers

Used by MCP integration tests to verify stdio transport, tool discovery,
and tool execution without external dependencies.

Usage:
    python tests/fixtures/simple_mcp_server.py
"""

import logging

from mcp.server import MCPServer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server using MCP 2.x API
mcp = MCPServer("test-server")


@mcp.tool()
def echo(message: str) -> str:
    """Echo back the input message.
    
    Args:
        message: Message to echo back
    """
    logger.info(f"echo called with message: {message}")
    return f"Echo: {message}"


@mcp.tool()
def add(a: float, b: float) -> str:
    """Add two numbers together.
    
    Args:
        a: First number
        b: Second number
    """
    logger.info(f"add called with a={a}, b={b}")
    result = a + b
    return f"Result: {result}"


if __name__ == "__main__":
    logger.info("Starting test MCP server...")
    mcp.run(transport="stdio")
