"""MCP Calendar Server - Streamable HTTP Transport.

Exposes calendar management tools via Model Context Protocol over Streamable
HTTP on /mcp, plus a /health endpoint for the Docker healthcheck.
"""

import os

import uvicorn
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

import calendar_tools


# Server configuration
SERVER_NAME = "calendar"
SERVER_VERSION = "1.0.0"
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "3000"))

# The endpoint clients connect to. Must match the url in workspace/.nikola/mcp.json.
MCP_PATH = "/mcp"


# Define available tools
TOOLS = [
    Tool(
        name="current_datetime",
        description=(
            "Get the current date, time, and weekday. Call this before resolving "
            "any relative date such as 'today', 'tomorrow', or 'next Thursday'."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="list_events",
        description=(
            "List calendar events, optionally limited to a date range. "
            "Each line ends with the short id needed to change that event."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "Range start, YYYY-MM-DD or YYYY-MM-DD HH:MM. Optional.",
                },
                "end": {
                    "type": "string",
                    "description": (
                        "Range end, YYYY-MM-DD or YYYY-MM-DD HH:MM. A bare date "
                        "includes that whole day. Optional."
                    ),
                },
            },
        },
    ),
    Tool(
        name="find_free_slots",
        description=(
            "Report the gaps on one day long enough for a given duration. "
            "Use this before proposing a time to the user."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "day": {"type": "string", "description": "The day, YYYY-MM-DD."},
                "duration_minutes": {
                    "type": "integer",
                    "description": "Minimum slot length in minutes. Defaults to 60.",
                },
                "day_start": {
                    "type": "string",
                    "description": "Earliest time to consider, HH:MM. Defaults to 09:00.",
                },
                "day_end": {
                    "type": "string",
                    "description": "Latest time to consider, HH:MM. Defaults to 18:00.",
                },
            },
            "required": ["day"],
        },
    ),
    Tool(
        name="create_event",
        description=(
            "Add an event to the calendar. Warns if it overlaps an existing event. "
            "Confirm the details with the user before calling this."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "What the event is called."},
                "start": {
                    "type": "string",
                    "description": "Start time, YYYY-MM-DD HH:MM.",
                },
                "end": {
                    "type": "string",
                    "description": "End time, YYYY-MM-DD HH:MM. Defaults to one hour after start.",
                },
                "location": {"type": "string", "description": "Where it happens. Optional."},
                "description": {"type": "string", "description": "Longer notes. Optional."},
            },
            "required": ["title", "start"],
        },
    ),
    Tool(
        name="update_event",
        description=(
            "Change an existing event. Give only the fields that change; moving the "
            "start alone keeps the original duration. Confirm with the user first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "The id from list_events. The short form is enough.",
                },
                "title": {"type": "string", "description": "New title. Optional."},
                "start": {"type": "string", "description": "New start, YYYY-MM-DD HH:MM. Optional."},
                "end": {"type": "string", "description": "New end, YYYY-MM-DD HH:MM. Optional."},
                "location": {"type": "string", "description": "New location. Optional."},
                "description": {"type": "string", "description": "New notes. Optional."},
            },
            "required": ["event_id"],
        },
    ),
]


async def handle_list_tools(
    context: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListToolsResult:
    """Return the list of available calendar tools."""
    return ListToolsResult(tools=TOOLS)


async def handle_call_tool(
    context: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult:
    """Dispatch tool calls to the appropriate calendar function."""
    name = params.name
    arguments = params.arguments or {}

    try:
        if name == "current_datetime":
            result = await calendar_tools.current_datetime()
        elif name == "list_events":
            result = await calendar_tools.list_events(
                start=arguments.get("start"),
                end=arguments.get("end"),
            )
        elif name == "find_free_slots":
            result = await calendar_tools.find_free_slots(
                day=arguments["day"],
                duration_minutes=arguments.get("duration_minutes", 60),
                day_start=arguments.get("day_start", calendar_tools.DEFAULT_DAY_START),
                day_end=arguments.get("day_end", calendar_tools.DEFAULT_DAY_END),
            )
        elif name == "create_event":
            result = await calendar_tools.create_event(
                title=arguments["title"],
                start=arguments["start"],
                end=arguments.get("end"),
                location=arguments.get("location"),
                description=arguments.get("description"),
            )
        elif name == "update_event":
            result = await calendar_tools.update_event(
                event_id=arguments["event_id"],
                title=arguments.get("title"),
                start=arguments.get("start"),
                end=arguments.get("end"),
                location=arguments.get("location"),
                description=arguments.get("description"),
            )
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool '{name}'")],
                is_error=True,
            )

        return CallToolResult(content=[TextContent(type="text", text=result)])

    except Exception as e:
        # Report the failure to the model rather than tearing down the session.
        error_msg = f"Error: {type(e).__name__}: {e}"
        return CallToolResult(
            content=[TextContent(type="text", text=error_msg)],
            is_error=True,
        )


# Initialize MCP server
server = Server(
    SERVER_NAME,
    version=SERVER_VERSION,
    on_list_tools=handle_list_tools,
    on_call_tool=handle_call_tool,
)


async def health_handler(request: Request) -> Response:
    """Health check endpoint for Docker healthcheck."""
    return PlainTextResponse("OK")


# The healthcheck rides along on the MCP app rather than a separate server: this
# app's lifespan owns the streamable-HTTP session manager, so building it here is
# what keeps the transport running.
app = server.streamable_http_app(
    streamable_http_path=MCP_PATH,
    # DNS rebinding protection is left off for 0.0.0.0: the agent reaches this
    # server by its Compose service name (mcp_calendar:3000), not a browser origin.
    host=MCP_HOST,
    custom_starlette_routes=[
        Route("/health", endpoint=health_handler, methods=["GET"]),
    ],
)


def main() -> None:
    """Start the MCP server with Streamable HTTP transport and health endpoint."""
    print(f"Starting MCP Calendar Server v{SERVER_VERSION}", flush=True)
    print(f"Listening on {MCP_HOST}:{MCP_PORT}", flush=True)
    print(f"Calendar path: {calendar_tools.get_calendar_path()}", flush=True)
    print(f"  - MCP endpoint:  {MCP_PATH}", flush=True)
    print(f"  - Health check:  /health", flush=True)

    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()
