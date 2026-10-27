"""Unit tests for HTTP MCP client."""

from unittest.mock import AsyncMock, patch

import pytest

from agent.mcp.http_client import HttpMCPClient


class TestHttpMCPClient:
    """Tests for HttpMCPClient."""

    def test_init(self):
        """Should initialize with server name and config."""
        config = {
            "type": "http",
            "url": "https://api.example.com/mcp",
            "headers": {"Authorization": "Bearer token"}
        }
        client = HttpMCPClient("test-server", config)
        
        assert client.server_name == "test-server"
        assert client.config == config
        assert client.connected is False
        assert client.disconnected is False
        assert client.client is None

    @pytest.mark.asyncio
    async def test_connect_missing_url_raises(self):
        """Should raise ValueError if url is missing."""
        config = {"type": "http", "headers": {}}
        client = HttpMCPClient("test-server", config)
        
        with pytest.raises(ValueError, match="missing 'url'"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Should successfully connect with valid config."""
        config = {
            "url": "https://api.example.com/mcp",
            "headers": {"Authorization": "Bearer token"}
        }
        client = HttpMCPClient("test-server", config)
        
        # Mock the MCP SDK components
        mock_mcp_client = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_mcp_client)
        
        with patch("agent.mcp.http_client.httpx.AsyncClient") as mock_http_client, \
             patch("agent.mcp.http_client.Client", return_value=mock_context):
            
            await client.connect()
            
            # Should create httpx client with headers and timeouts
            mock_http_client.assert_called_once()
            call_kwargs = mock_http_client.call_args.kwargs
            assert call_kwargs["headers"] == {"Authorization": "Bearer token"}
            assert call_kwargs["timeout"].connect == 10.0
            assert call_kwargs["timeout"].read == 300.0
            
            # Should be marked as connected
            assert client.connected is True
            assert client.disconnected is False

    @pytest.mark.asyncio
    async def test_connect_already_connected_skips(self):
        """Should skip if already connected."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        client.connected = True
        
        await client.connect()
        
        # Should remain connected, no new connection made
        assert client.connected is True

    @pytest.mark.asyncio
    async def test_disconnect_closes_clients(self):
        """Should close both MCP client and httpx client."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        
        # Simulate connected state
        client.connected = True
        mock_client_context = AsyncMock()
        mock_client_context.__aexit__ = AsyncMock()
        client._client_context = mock_client_context
        
        mock_http_client = AsyncMock()
        mock_http_client.aclose = AsyncMock()
        client._http_client = mock_http_client
        
        await client.disconnect()
        
        # Should exit MCP client context
        mock_client_context.__aexit__.assert_called_once()
        
        # Should close httpx client
        mock_http_client.aclose.assert_called_once()
        
        # Should clear state
        assert client.connected is False
        assert client._client_context is None
        assert client._http_client is None

    @pytest.mark.asyncio
    async def test_disconnect_not_connected_skips(self):
        """Should skip if not connected."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        
        await client.disconnect()
        
        # Should remain not connected
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_call_tool_when_disconnected(self):
        """Should return error string when disconnected."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        client.disconnected = True
        
        result = await client.call_tool("test_tool", {})
        
        assert "not responding" in result
        assert "test-server" in result

    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        """Should return error string and mark disconnected if client is None."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        client.connected = True
        client.client = None
        
        result = await client.call_tool("test_tool", {})
        
        assert "not responding" in result
        assert client.disconnected is True

    @pytest.mark.asyncio
    async def test_list_tools_when_disconnected_raises(self):
        """Should raise RuntimeError when disconnected."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        client.disconnected = True
        
        with pytest.raises(RuntimeError, match="disconnected"):
            await client.list_tools()

    @pytest.mark.asyncio
    async def test_url_sanitization_in_logs(self):
        """Should sanitize URL for logging (remove query params)."""
        config = {
            "url": "https://api.example.com/mcp?token=secret123",
            "headers": {}
        }
        client = HttpMCPClient("test-server", config)
        
        # Mock to capture what would be logged
        with patch("agent.mcp.http_client.httpx.AsyncClient"), \
             patch("agent.mcp.http_client.Client"), \
             patch("agent.mcp.http_client.logger") as mock_logger:
            
            try:
                await client.connect()
            except Exception:
                pass  # Expected to fail; we're only checking log output
            
            # Check that URL in log doesn't contain query params
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            for call in log_calls:
                if "Connecting" in call:
                    assert "secret123" not in call
                    assert "?" not in call or "token=" not in call


    @pytest.mark.asyncio
    async def test_connection_with_exception_group(self):
        """Should handle ExceptionGroup errors gracefully."""
        config = {"url": "https://api.example.com/mcp"}
        client = HttpMCPClient("test-server", config)
        
        # Create a mock ExceptionGroup (Python 3.11+)
        class MockExceptionGroup(Exception):
            """Mock ExceptionGroup for testing."""
            def __init__(self, message, exceptions):
                super().__init__(message)
                self.exceptions = exceptions
        
        underlying_error = ConnectionError("Connection refused")
        exception_group = MockExceptionGroup("unhandled errors", [underlying_error])
        
        # Create an AsyncMock for AsyncClient to support await aclose()
        mock_async_client = AsyncMock()
        mock_async_client.aclose = AsyncMock()
        
        with patch("agent.mcp.http_client.httpx.AsyncClient", return_value=mock_async_client), \
             patch("agent.mcp.http_client.Client") as MockClient:
            MockClient.side_effect = exception_group
            
            with pytest.raises(MockExceptionGroup):
                await client.connect()
            
            # Should be marked as disconnected
            assert client.disconnected is True
            # AsyncClient.aclose() should have been called during cleanup
            mock_async_client.aclose.assert_called_once()
