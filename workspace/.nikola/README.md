# MCP Server Configuration Guide

This directory contains the MCP (Model Context Protocol) server configuration for the agent harness.

## Quick Start

1. Copy the example configuration:
   ```bash
   cp .nikola/mcp.json.example .nikola/mcp.json
   ```

2. Set up environment variables for API keys:
   ```bash
   export CONTEXT7_API_KEY="your-context7-key"
   export BRAVE_API_KEY="your-brave-search-key"
   export GITHUB_TOKEN="your-github-token"
   ```

3. Enable only the servers you want by removing others from `mcp.json`

4. Start the agent - MCP servers will connect automatically

## Configuration Format

```json
{
  "mcpServers": {
    "server-name": {
      "command": "executable",
      "args": ["arg1", "arg2"],
      "env": {
        "ENV_VAR": "${ENV_VAR_NAME}"
      }
    }
  }
}
```

### Fields

- **server-name**: Unique identifier for the server (used in tool naming: `mcp_<server>__<tool>`)
- **command**: Executable to run (e.g. `npx`, `uvx`, `python`)
- **args**: Command-line arguments as a list
- **env**: Environment variables for the server process
  - Use `${VAR_NAME}` syntax to substitute from your environment
  - Literal values are passed as-is

## Example Servers

### Context7 - Documentation Search
Access up-to-date documentation for programming libraries and frameworks.

```json
"context7": {
  "command": "npx",
  "args": ["-y", "@upstash/context7-mcp"],
  "env": {
    "CONTEXT7_API_KEY": "${CONTEXT7_API_KEY}"
  }
}
```

**Get API key**: https://console.upstash.com/

**Tools provided**:
- `mcp_context7__resolve-library-id` - Find library documentation
- `mcp_context7__query-docs` - Search within library docs

### Filesystem - File Operations
Enhanced filesystem operations beyond built-in tools.

```json
"filesystem": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/directory"],
  "env": {}
}
```

**Note**: Specify the allowed directory as the last argument.

### Fetch - HTTP Requests
Make HTTP requests to fetch web content.

```json
"fetch": {
  "command": "uvx",
  "args": ["mcp-server-fetch"],
  "env": {}
}
```

**Requires**: Python with uvx installed (`pip install uv`)

### Brave Search - Web Search
Search the web using Brave Search API.

```json
"brave-search": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-brave-search"],
  "env": {
    "BRAVE_API_KEY": "${BRAVE_API_KEY}"
  }
}
```

**Get API key**: https://brave.com/search/api/

### GitHub - Repository Operations
Interact with GitHub repositories, issues, and pull requests.

```json
"github": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {
    "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
  }
}
```

**Get token**: https://github.com/settings/tokens (requires `repo` scope)

## Using MCP Tools

Once configured, MCP tools are automatically available in the agent:

1. **List MCP servers and tools**:
   ```
   /mcp
   ```

2. **List built-in tools** (MCP tools are separate):
   ```
   /tools
   ```

3. **Use MCP tools** in your prompts:
   ```
   Search the Next.js documentation for how to use Server Actions
   ```

The agent will automatically discover and call the appropriate MCP tool (e.g., `mcp_context7__query-docs`).

## Tool Naming Convention

MCP tools follow the pattern: `mcp_<server>__<tool>`

Examples:
- `mcp_context7__query-docs`
- `mcp_fetch__fetch`
- `mcp_github__create_issue`

This prevents naming collisions between servers and makes tool origin clear.

## Troubleshooting

### Server won't connect

1. **Check command is available**:
   ```bash
   which npx  # or uvx, python, etc.
   ```

2. **Test server manually**:
   ```bash
   npx -y @upstash/context7-mcp
   ```

3. **Check logs** - connection errors are logged but don't stop the session

### Environment variables not substituted

- Use `${VAR}` syntax (with braces)
- Check variable is exported: `echo $CONTEXT7_API_KEY`
- Restart the agent after setting new variables

### Tools not appearing

1. **Check server connected**:
   ```
   /mcp
   ```

2. **Look for "✓ Connected"** status

3. **If "✗ Failed"** - check server logs for errors

## Security Notes

- **Never commit `nikola/mcp.json`** if it contains secrets
- Use environment variable substitution for all sensitive values
- MCP servers run as subprocesses with your user permissions
- Only configure servers from trusted sources

## Disabling Servers

To temporarily disable a server without removing it:

1. Remove it from `mcp.json`, or
2. Comment out the entire server block (JSON doesn't support comments, so you'll need to remove it)

Alternatively, create multiple config files:
- `mcp.json` - active configuration
- `mcp-full.json` - all available servers
- `mcp-minimal.json` - essential servers only

Copy the one you want to `mcp.json`.

## Adding Custom MCP Servers

You can add any MCP-compatible server:

```json
"my-server": {
  "command": "python",
  "args": ["/path/to/my_mcp_server.py"],
  "env": {
    "MY_API_KEY": "${MY_API_KEY}"
  }
}
```

See [MCP documentation](https://modelcontextprotocol.io) for building custom servers.

## Week 2 Limitations

Current implementation (Week 2):
- ✅ Stdio transport (subprocess-based servers)
- ✅ Environment variable substitution
- ✅ Graceful failure handling
- ❌ HTTP transport (planned for future)
- ❌ Server auto-reconnection (planned for future)
- ❌ Dynamic server management via commands (read-only config)

## Getting Help

- Run `/help` in the agent to see all commands
- Run `/mcp` to check server status
- See `AGENTS.md` for architecture details
