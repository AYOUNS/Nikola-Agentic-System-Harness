# Calendar MCP Server - Integration Guide

## Overview

The calendar MCP server is now integrated as a **built-in tool** in the agent harness. It provides calendar management capabilities through five tools:

- `mcp_calendar__current_datetime`
- `mcp_calendar__list_events`
- `mcp_calendar__find_free_slots`
- `mcp_calendar__create_event`
- `mcp_calendar__update_event`

## Docker Compose Integration

### Service Configuration

The calendar server runs as a standalone service in `compose.yaml`:

```yaml
mcp_calendar:
  build:
    context: ./mcp-server-calendar
    dockerfile: Dockerfile
  environment:
    - AGENT_WORKSPACE=/workspace
    - MCP_HOST=0.0.0.0
    - MCP_PORT=3000
  volumes:
    - ./workspace:/workspace
  ports:
    - "127.0.0.1:3000:3000"
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:3000/health\")' || exit 0"]
    interval: 10s
    timeout: 5s
    retries: 3
```

### Dependencies

The agent service depends on the calendar server being healthy:

```yaml
agent:
  depends_on:
    mcp_calendar:
      condition: service_healthy
```

## MCP Configuration

The calendar server is configured in `workspace/.nikola/mcp.json`:

```json
{
  "mcpServers": {
    "calendar": {
      "type": "http",
      "url": "http://mcp_calendar:3000/sse",
      "description": "Calendar management tools - schedule events, find free slots, and manage appointments"
    }
  }
}
```

### Configuration Fields

- **`type`**: Transport type, must be `"http"` for SSE-based servers
- **`url`**: SSE endpoint URL, uses Docker Compose service name `mcp_calendar`
- **`description`**: Human-readable description (optional)

## Health Endpoint

The server exposes a health check endpoint at `/health` that returns `200 OK` when ready. This is used by Docker Compose to ensure the service is available before starting the agent.

## Tool Naming Convention

MCP tools are registered with the prefix `mcp_<server>__<tool>`:

- MCP server name: `calendar`
- Tool name: `current_datetime`
- Registered as: `mcp_calendar__current_datetime`

This naming prevents collisions with built-in tools and clearly identifies the tool's source.

## Calendar Data Storage

Calendar events are stored in iCalendar format at:
```
workspace/calendar.ics
```

This file:
- Persists across container restarts (mounted volume)
- Is created automatically on first `create_event` call
- Uses standard iCalendar format (RFC 5545)
- Can be imported into other calendar applications

## Architecture

```
┌─────────────────────────────────────────────────┐
│ Agent Harness Container                         │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ ReAct Loop                               │  │
│  │                                          │  │
│  │  ┌────────────────────────────────────┐ │  │
│  │  │ ToolRegistry                       │ │  │
│  │  │                                    │ │  │
│  │  │ • Built-in tools (filesystem)     │ │  │
│  │  │ • Memory tools                     │ │  │
│  │  │ • MCP tools (calendar)  ←─────────┼─┼──┼─── HTTP/SSE
│  │  └────────────────────────────────────┘ │  │     
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ MCPManager                               │  │
│  │                                          │  │
│  │  • Connects to MCP servers              │  │
│  │  • Discovers tools                      │  │
│  │  • Registers with naming prefix         │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
                      │
                      │ HTTP/SSE
                      ▼
┌─────────────────────────────────────────────────┐
│ MCP Calendar Server Container                   │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ aiohttp Web Server                       │  │
│  │  • /sse   — SSE endpoint (MCP protocol) │  │
│  │  • /health — Health check               │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ MCP Server                               │  │
│  │  • list_tools()                          │  │
│  │  • call_tool()                           │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ calendar_tools.py                        │  │
│  │  • current_datetime()                    │  │
│  │  • list_events()                         │  │
│  │  • find_free_slots()                     │  │
│  │  • create_event()                        │  │
│  │  • update_event()                        │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ workspace/calendar.ics (shared volume)   │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## Local Development (Without Docker)

To run the calendar server standalone for development:

```bash
cd mcp-server-calendar
pip install -r requirements.txt
export AGENT_WORKSPACE=../workspace
python server.py
```

Then update `workspace/.nikola/mcp.json` to point to localhost:

```json
{
  "mcpServers": {
    "calendar": {
      "type": "http",
      "url": "http://localhost:3000/sse"
    }
  }
}
```

## Testing

### Verify Service is Running

```bash
# Check health endpoint
curl http://localhost:3000/health

# Should return: OK
```

### Verify Agent Connection

Start the agent and run:
```
/mcp
```

You should see:
```
MCP Server Status:
  calendar: connected, 5 tools
```

### List Calendar Tools

```
/tools
```

You should see the five calendar tools with `mcp_calendar__` prefix.

### Test Calendar Operations

```
Agent: What's the current date and time?
# Should call mcp_calendar__current_datetime

Agent: Create an event for tomorrow at 2pm titled "Team Meeting"
# Should call mcp_calendar__current_datetime and mcp_calendar__create_event

Agent: What events do I have this week?
# Should call mcp_calendar__list_events with date range
```

## Troubleshooting

### Calendar server not starting

Check logs:
```bash
docker compose logs mcp_calendar
```

Common issues:
- Port 3000 already in use
- Missing aiohttp dependency (rebuild container)

### Tools not appearing in agent

1. Check MCP connection:
   ```
   /mcp
   ```

2. Verify config file exists:
   ```bash
   cat workspace/.nikola/mcp.json
   ```

3. Check agent logs for MCP connection errors:
   ```bash
   docker compose logs agent
   ```

### Calendar file not persisting

Ensure the workspace volume is mounted correctly:
```bash
docker compose down
docker compose up --build
```

Check that `workspace/calendar.ics` exists on the host after creating an event.

## Week 2 Compliance

This integration satisfies the Week 2 MCP requirements:

✅ **MCP server integration** — HTTP transport, SSE protocol  
✅ **Custom MCP tool** — Calendar management (non-trivial, non-filesystem domain)  
✅ **Tool discovery** — Automatic at startup via MCPManager  
✅ **Tool registration** — Namespaced as `mcp_<server>__<tool>`  
✅ **Error handling** — Graceful connection failure handling  
✅ **Configuration** — External config in `workspace/.nikola/mcp.json`  
✅ **Built-in availability** — Starts with compose stack, no manual setup  

## Security Notes

### Network Binding

The calendar server binds to `0.0.0.0` inside the container but the port mapping in compose.yaml restricts external access:

```yaml
ports:
  - "127.0.0.1:3000:3000"  # Only accessible from localhost
```

This follows the security rule: **MUST use localhost (127.0.0.1) or specific internal IPs for service binding**.

### Container Base Image

Uses `python:3.12-slim-bookworm` as required by security rules:
- ✅ Python 3.12 only
- ✅ Debian-based official image
- ✅ Minimal attack surface

### No Hardcoded Secrets

- ✅ No API keys or tokens in code
- ✅ Workspace path from environment variable
- ✅ Configuration from external file

## Future Enhancements

Potential improvements (out of scope for Week 2):

- **Recurring events** — Support for RRULE in iCalendar
- **Event deletion** — Add `delete_event` tool (currently read/create/update only)
- **Multiple calendars** — Support for separate calendar files
- **Event reminders** — Notification system for upcoming events
- **Calendar import/export** — Bulk operations
- **Time zone support** — Currently assumes local time
