"""Unit tests for MCP configuration loader with transport-aware validation."""

import json
import os
import tempfile
from pathlib import Path

from agent.mcp.config import (
    is_loopback,
    load_mcp_config,
    process_env_dict,
    substitute_env_vars,
    validate_http_url,
)


class TestSubstituteEnvVars:
    """Tests for environment variable substitution."""

    def test_substitute_existing_var(self):
        """Should replace ${VAR} with environment variable value."""
        os.environ["TEST_VAR"] = "test-value"
        result = substitute_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_test-value_suffix"

    def test_substitute_missing_var(self):
        """Should replace missing ${VAR} with empty string."""
        # Make sure var doesn't exist
        os.environ.pop("NONEXISTENT_VAR", None)
        result = substitute_env_vars("prefix_${NONEXISTENT_VAR}_suffix")
        assert result == "prefix__suffix"

    def test_no_substitution_without_braces(self):
        """Should not touch literal strings without ${} syntax."""
        result = substitute_env_vars("literal-value")
        assert result == "literal-value"

    def test_multiple_substitutions(self):
        """Should handle multiple ${VAR} patterns."""
        os.environ["VAR1"] = "value1"
        os.environ["VAR2"] = "value2"
        result = substitute_env_vars("${VAR1}-${VAR2}")
        assert result == "value1-value2"


class TestProcessEnvDict:
    """Tests for processing environment variable dictionaries."""

    def test_process_string_values(self):
        """Should substitute ${VAR} in string values."""
        os.environ["API_KEY"] = "secret123"
        env = {"key": "${API_KEY}", "literal": "value"}
        result = process_env_dict(env)
        assert result == {"key": "secret123", "literal": "value"}

    def test_process_non_string_values(self):
        """Should convert non-string values to strings."""
        env = {"port": 8080, "enabled": True}
        result = process_env_dict(env)
        assert result == {"port": "8080", "enabled": "True"}

    def test_empty_dict(self):
        """Should handle empty dict."""
        result = process_env_dict({})
        assert result == {}


class TestIsLoopback:
    """Tests for loopback address detection."""

    def test_localhost(self):
        """Should recognize localhost."""
        assert is_loopback("localhost") is True
        assert is_loopback("LOCALHOST") is True

    def test_ipv4_loopback(self):
        """Should recognize 127.0.0.1."""
        assert is_loopback("127.0.0.1") is True

    def test_ipv6_loopback(self):
        """Should recognize ::1 and [::1]."""
        assert is_loopback("::1") is True
        assert is_loopback("[::1]") is True

    def test_non_loopback(self):
        """Should reject non-loopback addresses."""
        assert is_loopback("example.com") is False
        assert is_loopback("192.168.1.1") is False
        assert is_loopback("10.0.0.1") is False


class TestValidateHttpUrl:
    """Tests for HTTP URL validation."""

    def test_https_remote_allowed(self):
        """Should allow https:// for any host."""
        assert validate_http_url("https://example.com/mcp", "test") is True
        assert validate_http_url("https://api.example.com:8080/mcp", "test") is True

    def test_http_localhost_allowed(self):
        """Should allow http:// for localhost."""
        assert validate_http_url("http://localhost:8080/mcp", "test") is True
        assert validate_http_url("http://127.0.0.1:8080/mcp", "test") is True

    def test_http_remote_rejected(self):
        """Should reject http:// for non-loopback hosts."""
        assert validate_http_url("http://example.com/mcp", "test") is False
        assert validate_http_url("http://192.168.1.1/mcp", "test") is False

    def test_invalid_scheme_rejected(self):
        """Should reject non-http(s) schemes."""
        assert validate_http_url("ftp://example.com", "test") is False
        assert validate_http_url("ws://example.com", "test") is False

    def test_malformed_url_rejected(self):
        """Should reject malformed URLs."""
        assert validate_http_url("not-a-url", "test") is False


class TestAllowInsecureHttpHosts:
    """Tests for the MCP_ALLOW_INSECURE_HTTP_HOSTS opt-in."""

    def test_allowlisted_host_accepted(self, monkeypatch):
        """Should allow http:// to a host named in the allowlist."""
        monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP_HOSTS", "mcp_calendar")
        assert validate_http_url("http://mcp_calendar:3000/mcp", "test") is True

    def test_strict_by_default(self, monkeypatch):
        """Unset allowlist must keep the loopback-only policy."""
        monkeypatch.delenv("MCP_ALLOW_INSECURE_HTTP_HOSTS", raising=False)
        assert validate_http_url("http://mcp_calendar:3000/mcp", "test") is False

    def test_allowlist_does_not_leak_to_other_hosts(self, monkeypatch):
        """Allowlisting one host must not permit any other host."""
        monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP_HOSTS", "mcp_calendar")
        assert validate_http_url("http://evil.example.com/mcp", "test") is False

    def test_multiple_hosts_and_whitespace(self, monkeypatch):
        """Should parse a comma-separated list, ignoring case and padding."""
        monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP_HOSTS", " mcp_calendar , OTHER_SVC ")
        assert validate_http_url("http://other_svc:9000/mcp", "test") is True
        assert validate_http_url("http://mcp_calendar:3000/mcp", "test") is True

    def test_allowlist_does_not_bypass_scheme_check(self, monkeypatch):
        """A non-http(s) scheme stays rejected even for an allowlisted host."""
        monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP_HOSTS", "mcp_calendar")
        assert validate_http_url("ftp://mcp_calendar/mcp", "test") is False

    def test_empty_value_is_strict(self, monkeypatch):
        """An empty allowlist must not permit anything."""
        monkeypatch.setenv("MCP_ALLOW_INSECURE_HTTP_HOSTS", "  ,  ")
        assert validate_http_url("http://mcp_calendar:3000/mcp", "test") is False


class TestLoadMcpConfig:
    """Tests for loading and validating MCP configuration."""

    def test_missing_config_returns_empty(self):
        """Should return empty dict when config file doesn't exist."""
        with tempfile.TemporaryDirectory() as workspace:
            result = load_mcp_config(workspace)
            assert result == {}

    def test_invalid_json_returns_empty(self):
        """Should return empty dict and log error for invalid JSON."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config_path = nikola_dir / "mcp.json"
            config_path.write_text("not valid json")

            result = load_mcp_config(workspace)
            assert result == {}

    def test_stdio_server_accepted(self):
        """Should accept stdio server with command and args."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "test-stdio": {
                        "command": "python",
                        "args": ["-m", "mcp_server"],
                        "env": {"KEY": "value"}
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert "test-stdio" in result
            assert result["test-stdio"]["type"] == "stdio"
            assert result["test-stdio"]["command"] == "python"
            assert result["test-stdio"]["args"] == ["-m", "mcp_server"]

    def test_http_server_accepted(self):
        """Should accept http server with url and headers."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            os.environ["API_TOKEN"] = "secret123"
            config = {
                "mcpServers": {
                    "test-http": {
                        "type": "http",
                        "url": "https://api.example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer ${API_TOKEN}"
                        }
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert "test-http" in result
            assert result["test-http"]["type"] == "http"
            assert result["test-http"]["url"] == "https://api.example.com/mcp"
            assert result["test-http"]["headers"]["Authorization"] == "Bearer secret123"

    def test_type_inference_from_command(self):
        """Should infer type=stdio from presence of command."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "inferred-stdio": {
                        "command": "uvx",
                        "args": ["mcp-server"]
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert result["inferred-stdio"]["type"] == "stdio"

    def test_type_inference_from_url(self):
        """Should infer type=http from presence of url."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "inferred-http": {
                        "url": "https://example.com/mcp"
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert result["inferred-http"]["type"] == "http"

    def test_both_command_and_url_rejected(self):
        """Should reject server with both command and url."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "ambiguous": {
                        "command": "python",
                        "url": "https://example.com/mcp"
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert "ambiguous" not in result

    def test_neither_command_nor_url_rejected(self):
        """Should reject server with neither command nor url."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "empty": {
                        "env": {}
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert "empty" not in result

    def test_disabled_server_skipped(self):
        """Should skip servers with disabled=true."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "disabled-server": {
                        "command": "python",
                        "args": [],
                        "disabled": True
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert "disabled-server" not in result

    def test_http_plaintext_remote_rejected(self):
        """Should reject http:// for non-loopback hosts."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "insecure": {
                        "type": "http",
                        "url": "http://example.com/mcp"
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert "insecure" not in result

    def test_header_substitution(self):
        """Should substitute ${VAR} in headers."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            os.environ["TOKEN1"] = "bearer-token"
            os.environ["TOKEN2"] = "api-key"
            config = {
                "mcpServers": {
                    "api": {
                        "url": "https://api.example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer ${TOKEN1}",
                            "X-API-Key": "${TOKEN2}"
                        }
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert result["api"]["headers"]["Authorization"] == "Bearer bearer-token"
            assert result["api"]["headers"]["X-API-Key"] == "api-key"

    def test_multiple_servers_mixed_transports(self):
        """Should handle multiple servers with different transports."""
        with tempfile.TemporaryDirectory() as workspace:
            nikola_dir = Path(workspace) / ".nikola"
            nikola_dir.mkdir()
            config = {
                "mcpServers": {
                    "local-stdio": {
                        "command": "python",
                        "args": ["-m", "server"]
                    },
                    "remote-http": {
                        "url": "https://api.example.com/mcp"
                    },
                    "local-http": {
                        "url": "http://localhost:8080/mcp"
                    }
                }
            }
            config_path = nikola_dir / "mcp.json"
            config_path.write_text(json.dumps(config))

            result = load_mcp_config(workspace)
            assert len(result) == 3
            assert result["local-stdio"]["type"] == "stdio"
            assert result["remote-http"]["type"] == "http"
            assert result["local-http"]["type"] == "http"
