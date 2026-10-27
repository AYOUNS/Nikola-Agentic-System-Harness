"""Simple HTTP MCP server for testing.

This is a local HTTP MCP server that demonstrates the HTTP transport.
Run with: uvicorn simple_http_mcp_server:app --host localhost --port 8080
"""


from datetime import UTC

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Route

# Create MCP server
server = Server("simple-http-test")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_time",
            description="Get the current server time",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="calculate",
            description="Perform a calculation (add, subtract, multiply, divide)",
            inputSchema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "Operation to perform",
                        "enum": ["add", "subtract", "multiply", "divide"]
                    },
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"}
                },
                "required": ["operation", "a", "b"]
            }
        ),
        Tool(
            name="reverse_string",
            description="Reverse a string",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "String to reverse"}
                },
                "required": ["text"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    if name == "get_time":
        from datetime import datetime
        return [TextContent(
            type="text",
            text=f"Current time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}"
        )]
    
    elif name == "calculate":
        operation = arguments["operation"]
        a = arguments["a"]
        b = arguments["b"]
        
        operations = {
            "add": lambda x, y: x + y,
            "subtract": lambda x, y: x - y,
            "multiply": lambda x, y: x * y,
            "divide": lambda x, y: x / y if y != 0 else None
        }
        
        if operation not in operations:
            return [TextContent(type="text", text=f"Error: Unknown operation '{operation}'")]
        
        result = operations[operation](a, b)
        if result is None:
            return [TextContent(type="text", text="Error: Division by zero")]
        
        return [TextContent(type="text", text=f"{a} {operation} {b} = {result}")]
    
    elif name == "reverse_string":
        text = arguments["text"]
        return [TextContent(type="text", text=text[::-1])]
    
    else:
        return [TextContent(type="text", text=f"Error: Unknown tool '{name}'")]


# Create Starlette app with SSE endpoint
async def handle_sse(request):
    """Handle SSE connection for MCP."""
    async with SseServerTransport("/messages") as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse)
    ]
)


if __name__ == "__main__":
    import uvicorn
    print("Starting Simple HTTP MCP Server on http://localhost:8080")
    print("SSE endpoint: http://localhost:8080/sse")
    print("Press Ctrl+C to stop")
    uvicorn.run(app, host="localhost", port=8080)
