# Use Case: Custom MCP Tool

The harness is not limited to the built-in calendar server — any MCP-compatible server
can be connected. This use case describes what makes a good custom MCP tool and how to
add one.

---

## What the custom tool must satisfy

Per the project spec, a new MCP tool must:

- Expose a capability **not already available** in the existing MCP server
- Address a **non-trivial use case unrelated to programming or filesystem manipulation**
- Have a **bounded, clearly described capability** with full schema, validation, error handling,
  and tests

The following do **not** satisfy the requirement:

- Generic shell execution
- Arbitrary code evaluation
- Unrestricted HTTP requests

---

## Adding a custom MCP server

### 1 — Implement the server

The MCP SDK supports Python, TypeScript, and other languages. A minimal Python HTTP server:

```python
from mcp.server.fastmcp import FastMCP

app = FastMCP("my-tool")

@app.tool()
def my_tool(input: str) -> str:
    """A bounded, clearly described capability."""
    # ... implementation
    return result

if __name__ == "__main__":
    app.run(transport="http", port=4000)
```

### 2 — Add to compose

```yaml
# In compose.yaml
my_mcp_server:
  build: ./my-mcp-server
  ports:
    - "4000:4000"
```

### 3 — Register in `.nikola/mcp.json`

```json
{
  "mcpServers": {
    "my-tool": {
      "type": "http",
      "url": "http://my_mcp_server:4000/mcp",
      "description": "Brief description of what this server does"
    }
  }
}
```

Restart the agent — `docker compose restart agent && docker compose attach agent`
(or `podman compose restart agent && podman compose attach agent`) — and
the tool will appear in `/tools` as `mcp_my-tool__my_tool`.

---

## Permissions

By default the new MCP tool falls through to `default: ask`. To auto-allow it:

```yaml
- tool: "mcp_my-tool__*"
  action: allow
```

---

## The calendar server as a reference implementation

The project's custom MCP tool is the **calendar server** in `mcp-server-calendar/`. It:

- Manages a local `.ics` file (iCalendar format)
- Exposes create, read, update, and free-slot-finding operations
- Is non-trivial: it parses dates, validates overlaps, and serialises to a standard format
- Is entirely unrelated to filesystem manipulation or programming

See `mcp-server-calendar/INTEGRATION.md` for the full implementation walkthrough.
