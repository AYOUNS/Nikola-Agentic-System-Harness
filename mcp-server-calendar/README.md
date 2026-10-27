# MCP Calendar Server

Calendar management via Model Context Protocol over SSE (Server-Sent Events).

## Overview

Exposes five calendar tools through MCP:
- `current_datetime` — get current date, time, and weekday
- `list_events` — list events with optional date range filter
- `find_free_slots` — find available time slots on a given day
- `create_event` — add a new calendar event
- `update_event` — modify an existing event

## Setup

### Local Development

```bash
pip install -r requirements.txt
python server.py
```

### Docker

```bash
docker build -t mcp-calendar .
docker run -p 3000:3000 -v ./workspace:/workspace mcp-calendar
```

### Docker Compose

See root `compose.yaml` for integration with the agent harness.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_WORKSPACE` | `/workspace` | Workspace directory (calendar at `{workspace}/calendar.ics`) |
| `MCP_HOST` | `0.0.0.0` | Host to bind the SSE server |
| `MCP_PORT` | `3000` | Port for the SSE endpoint |

### Calendar File

The calendar file is stored at `{AGENT_WORKSPACE}/calendar.ics` in iCalendar format. It is created automatically on the first `create_event` call if it doesn't exist.

## Tools

### current_datetime

Get the current date, time, and day of the week.

**Parameters:** None

**Returns:** String like `"2026-09-10 14:30 (Wednesday)"`

### list_events

List calendar events, optionally filtered by date range.

**Parameters:**
- `start` (optional): Range start, `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`
- `end` (optional): Range end, `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`

**Returns:** List of events with short IDs for updating

### find_free_slots

Find available time slots on a specific day.

**Parameters:**
- `day` (required): The day to check, `YYYY-MM-DD`
- `duration_minutes` (optional, default: 60): Minimum slot length
- `day_start` (optional, default: `09:00`): Earliest time to consider
- `day_end` (optional, default: `18:00`): Latest time to consider

**Returns:** List of free time slots

### create_event

Add a new event to the calendar.

**Parameters:**
- `title` (required): Event title
- `start` (required): Start time, `YYYY-MM-DD HH:MM`
- `end` (optional): End time, defaults to 1 hour after start
- `location` (optional): Event location
- `description` (optional): Event description

**Returns:** Confirmation with event ID, warns if overlapping events exist

### update_event

Modify an existing event.

**Parameters:**
- `event_id` (required): Event ID from `list_events` (short form accepted)
- `title` (optional): New title
- `start` (optional): New start time (keeps duration if `end` not specified)
- `end` (optional): New end time
- `location` (optional): New location
- `description` (optional): New description

**Returns:** Confirmation with updated details, warns if overlapping events exist

## Architecture

- **Language:** Python 3.12
- **MCP SDK:** `mcp>=2.2.0`
- **Transport:** SSE (Server-Sent Events over HTTP)
- **Calendar format:** iCalendar (.ics)
- **Async I/O:** `aiofiles` for non-blocking file operations

## Error Handling

All tools return results as strings. Errors are prefixed with `"Error: "` rather than raising exceptions, allowing the LLM to read and reason about failures.
