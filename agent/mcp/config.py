"""Configuration loader for nikola/mcp.json.

Handles:
- Loading and parsing MCP server configuration
- Environment variable substitution using ${VAR} syntax
- Validation of required fields (transport-aware)
- Support for stdio and HTTP transports
- Graceful handling of missing or invalid config files
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

TransportType = Literal["stdio", "http"]


def substitute_env_vars(value: str) -> str:
    """Replace ${VAR} patterns with environment variable values.
    
    Per design Question 7 (Option A):
    - Only ${VAR} syntax is supported (explicit opt-in)
    - Missing variables are replaced with empty string
    - Literal strings without ${} are unchanged
    
    Examples:
        "${API_KEY}" -> value of API_KEY env var
        "hardcoded-value" -> "hardcoded-value" (unchanged)
        "prefix_${VAR}_suffix" -> "prefix_<value>_suffix"
    """
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    
    # Match ${VARIABLE_NAME} - uppercase letters, digits, underscores
    pattern = r'\$\{([A-Z_][A-Z0-9_]*)\}'
    return re.sub(pattern, replacer, value)


def process_env_dict(env: dict[str, Any]) -> dict[str, str]:
    """Process environment variable dict, substituting ${VAR} patterns.
    
    env: Dict from mcp.json server config
    
    Returns:
        Dict with all ${VAR} patterns replaced
    """
    processed = {}
    for key, value in env.items():
        if isinstance(value, str):
            processed[key] = substitute_env_vars(value)
        else:
            processed[key] = str(value)
    return processed


def is_loopback(hostname: str) -> bool:
    """Check if hostname refers to loopback address.
    
    Returns True for localhost, 127.0.0.1, ::1, and [::1]
    """
    hostname = hostname.lower()
    return hostname in ("localhost", "127.0.0.1", "::1", "[::1]")


def allowed_insecure_http_hosts() -> set[str]:
    """Hostnames permitted to use http:// despite not being loopback.

    Read from MCP_ALLOW_INSECURE_HTTP_HOSTS as a comma-separated list. This is
    the opt-in for container networks, where a service is addressed by an
    internal DNS name (e.g. a Docker Compose service) and the traffic never
    leaves the bridge. Unset means loopback-only, the strict default.
    """
    raw = os.environ.get("MCP_ALLOW_INSECURE_HTTP_HOSTS", "")
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def validate_http_url(url: str, server_name: str) -> bool:
    """Validate HTTP URL for MCP server.

    Rules:
    - Must parse as valid URL
    - Must have http or https scheme
    - http:// only allowed for loopback addresses, or for hosts explicitly
      listed in MCP_ALLOW_INSECURE_HTTP_HOSTS
    - https:// allowed for any address

    Returns True if valid, False otherwise (logs warning)
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            logger.warning(
                f"Skipping MCP server '{server_name}': URL scheme must be http or https, got '{parsed.scheme}'"
            )
            return False

        # Reject http:// for non-loopback hosts, unless explicitly allowlisted
        if parsed.scheme == "http" and not is_loopback(parsed.hostname or ""):
            hostname = (parsed.hostname or "").lower()
            if hostname not in allowed_insecure_http_hosts():
                logger.warning(
                    f"Skipping MCP server '{server_name}': http:// is only allowed for loopback addresses "
                    f"(localhost, 127.0.0.1, ::1). Use https:// for remote hosts, set hostname to localhost, "
                    f"or add '{hostname}' to MCP_ALLOW_INSECURE_HTTP_HOSTS if it is on a trusted internal network."
                )
                return False
            logger.info(
                f"MCP server '{server_name}': allowing cleartext http:// to '{hostname}' "
                f"(listed in MCP_ALLOW_INSECURE_HTTP_HOSTS)"
            )

        return True
    
    except Exception as e:
        logger.warning(f"Skipping MCP server '{server_name}': invalid URL '{url}': {e}")
        return False


def infer_transport_type(server_config: dict[str, Any], server_name: str) -> TransportType | None:
    """Infer transport type from server config.
    
    Rules:
    - If 'type' is explicitly set, use it
    - If 'command' is present, infer 'stdio'
    - If 'url' is present, infer 'http'
    - If both command and url are present, error
    - If neither command nor url are present, error
    
    Returns transport type or None if invalid (logs warning)
    """
    explicit_type = server_config.get("type")
    has_command = "command" in server_config
    has_url = "url" in server_config
    
    # Explicit type takes precedence
    if explicit_type is not None:
        if explicit_type not in ("stdio", "http"):
            logger.warning(
                f"Skipping MCP server '{server_name}': invalid type '{explicit_type}', "
                f"must be 'stdio' or 'http'"
            )
            return None
        return explicit_type
    
    # Infer from presence of command or url
    if has_command and has_url:
        logger.warning(
            f"Skipping MCP server '{server_name}': cannot specify both 'command' and 'url'. "
            f"Use 'type' to disambiguate or provide only one."
        )
        return None
    
    if has_command:
        return "stdio"
    
    if has_url:
        return "http"
    
    logger.warning(
        f"Skipping MCP server '{server_name}': must specify either 'command' (for stdio) "
        f"or 'url' (for http transport)"
    )
    return None


def validate_and_process_server(
    server_name: str, server_config: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate and process a single server configuration.
    
    Returns processed config dict or None if invalid (logs warning)
    """
    if not isinstance(server_config, dict):
        logger.warning(f"Skipping MCP server '{server_name}': config must be an object")
        return None
    
    # Check if disabled
    if server_config.get("disabled", False):
        logger.info(f"Skipping MCP server '{server_name}': disabled in config")
        return None
    
    # Infer or validate transport type
    transport_type = infer_transport_type(server_config, server_name)
    if transport_type is None:
        return None
    
    # Branch validation based on transport type
    if transport_type == "stdio":
        return validate_and_process_stdio(server_name, server_config)
    else:  # http
        return validate_and_process_http(server_name, server_config)


def validate_and_process_stdio(
    server_name: str, server_config: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate and process stdio transport configuration."""
    if "command" not in server_config:
        hint = " (found 'url' instead)" if "url" in server_config else ""
        logger.warning(
            f"Skipping MCP server '{server_name}': stdio transport requires 'command' field{hint}"
        )
        return None
    
    args = server_config.get("args", [])
    if not isinstance(args, list):
        logger.warning(f"Skipping MCP server '{server_name}': 'args' must be a list")
        return None
    
    processed_config = {
        "type": "stdio",
        "command": server_config["command"],
        "args": args,
        "env": process_env_dict(server_config.get("env", {})),
    }
    
    logger.info(f"Loaded MCP server config: {server_name} (stdio)")
    return processed_config


def validate_and_process_http(
    server_name: str, server_config: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate and process HTTP transport configuration."""
    if "url" not in server_config:
        hint = " (found 'command' instead)" if "command" in server_config else ""
        logger.warning(
            f"Skipping MCP server '{server_name}': http transport requires 'url' field{hint}"
        )
        return None
    
    url = server_config["url"]
    if not isinstance(url, str):
        logger.warning(f"Skipping MCP server '{server_name}': 'url' must be a string")
        return None
    
    # Validate URL
    if not validate_http_url(url, server_name):
        return None
    
    # Process headers with environment variable substitution
    headers = server_config.get("headers", {})
    if not isinstance(headers, dict):
        logger.warning(f"Skipping MCP server '{server_name}': 'headers' must be an object")
        return None
    
    processed_config = {
        "type": "http",
        "url": url,
        "headers": process_env_dict(headers),
    }
    
    logger.info(f"Loaded MCP server config: {server_name} (http)")
    return processed_config


def load_mcp_config(workspace_root: str) -> dict[str, dict[str, Any]]:
    """Load MCP server configuration from nikola/mcp.json.
    
    Per design Question 11 (Option A):
    - Location: ${WORKSPACE}/nikola/mcp.json
    - Missing file: returns empty dict (no MCP servers, not an error)
    - Invalid JSON: logs error, returns empty dict
    - Validates required fields based on transport type
    - Processes env variable substitution for env dicts and headers
    
    Supports two transport types:
    - stdio: requires 'command', optional 'args' and 'env'
    - http: requires 'url', optional 'headers'
    
    Transport type can be:
    - Explicitly set via 'type' field
    - Inferred from presence of 'command' (stdio) or 'url' (http)
    
    workspace_root: Workspace root path (e.g. /workspace)
    
    Returns:
        Dict mapping server names to their config dicts:
        {
          "server-name": {
            "type": "stdio",
            "command": "uvx",
            "args": ["mcp-server-weather"],
            "env": {"API_KEY": "processed-value"}
          },
          "http-server": {
            "type": "http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer processed-token"}
          }
        }
    """
    config_path = Path(workspace_root) / ".nikola" / "mcp.json"
    
    # Missing file is not an error - just means no MCP servers configured
    if not config_path.exists():
        logger.info(f"No MCP config found at {config_path} - no MCP servers will be loaded")
        return {}
    
    logger.info(f"Loading MCP configuration from {config_path}")
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_config = json.load(f)
        
        # Expect Claude Desktop format: {"mcpServers": {...}}
        if not isinstance(raw_config, dict):
            logger.error(f"Invalid MCP config: root must be an object, got {type(raw_config).__name__}")
            return {}
        
        servers = raw_config.get("mcpServers", {})
        if not isinstance(servers, dict):
            logger.error(f"Invalid MCP config: mcpServers must be an object, got {type(servers).__name__}")
            return {}
        
        # Process each server config
        processed = {}
        for server_name, server_config in servers.items():
            processed_config = validate_and_process_server(server_name, server_config)
            if processed_config is not None:
                processed[server_name] = processed_config
        
        return processed
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse MCP config at {config_path}: {e}")
        return {}
    
    except Exception as e:
        logger.error(f"Error loading MCP config from {config_path}: {e}")
        return {}
