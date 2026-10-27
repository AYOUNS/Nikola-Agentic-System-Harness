# Use Case: Calendar Scheduling

The harness connects to the **MCP Calendar server** to create, list, and query calendar events.
This use case demonstrates MCP tool integration and the sub-agent `scheduler` role.

---

## Setup

The calendar server is included in the compose stack and starts automatically:

```bash
docker compose up --build -d
```

The server is available at <http://localhost:3000> and configured in
`workspace/.nikola/mcp.json` as:

```json
{
  "mcpServers": {
    "calendar": {
      "type": "http",
      "url": "http://mcp_calendar:3000/mcp"
    }
  }
}
```

Events are stored in `workspace/calendar.ics`.

---

## Permissions

MCP tools match no explicit rule by default and fall through to `default: ask`. To use
calendar tools without a prompt on every call, add to `permissions.yaml`:

```yaml
- tool: "mcp_calendar__*"
  action: allow
```

Without this rule the agent will prompt you for each calendar call — which is fine for the
demo, but inconvenient for the `scheduler` sub-agent role (which cannot prompt).

---

## Example prompts

**Schedule a meeting:**

```
Schedule a team standup tomorrow at 9am for 30 minutes called "Morning Standup".
```

**Check the calendar:**

```
What events do I have this week?
```

**Find a free slot:**

```
Find a free 1-hour slot sometime on Thursday afternoon.
```

**Delegate to the scheduler role:**

```
Use a scheduler sub-agent to check today's calendar and summarise what I have.
```

---

## Available MCP tools

Once the calendar server is connected, the following tools appear in `/tools`:

| Tool | Description |
|---|---|
| `mcp_calendar__create_event` | Create a new calendar event |
| `mcp_calendar__list_events` | List events in a date range |
| `mcp_calendar__get_event` | Get details of a specific event |
| `mcp_calendar__update_event` | Update an existing event |
| `mcp_calendar__find_free_slots` | Find available time slots |

---

## Verify

```bash
cat workspace/calendar.ics
```

The `.ics` file is standard iCalendar format — importable into any calendar application.
