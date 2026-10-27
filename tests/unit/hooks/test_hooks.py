"""Unit tests for observability hooks.

Tests the hook registry, event types, and logging hook implementation.
"""

from __future__ import annotations

import logging
from datetime import UTC
from unittest.mock import MagicMock, patch

from agent.hooks import (
    HookRegistry,
    LLMCallEvent,
    SubAgentEndEvent,
    SubAgentStartEvent,
    ToolCallEvent,
    get_hook_registry,
)
from agent.observability import LoggingHook

# ---------------------------------------------------------------------------
# HookRegistry Tests
# ---------------------------------------------------------------------------

class TestHookRegistry:
    """Tests for HookRegistry."""

    def test_register_hook(self):
        """Test that hooks can be registered."""
        registry = HookRegistry()
        
        class TestHook:
            def on_subagent_start(self, event):
                pass
        
        hook = TestHook()
        registry.register(hook)
        
        assert len(registry._hooks) == 1
        assert registry._hooks[0] is hook

    def test_register_multiple_hooks(self):
        """Test that multiple hooks can be registered."""
        registry = HookRegistry()
        
        class TestHook:
            def on_subagent_start(self, event):
                pass
        
        hook1 = TestHook()
        hook2 = TestHook()
        
        registry.register(hook1)
        registry.register(hook2)
        
        assert len(registry._hooks) == 2

    def test_clear_removes_all_hooks(self):
        """Test that clear() removes all hooks."""
        registry = HookRegistry()
        
        class TestHook:
            def on_subagent_start(self, event):
                pass
        
        registry.register(TestHook())
        registry.register(TestHook())
        
        assert len(registry._hooks) == 2
        
        registry.clear()
        
        assert len(registry._hooks) == 0

    def test_emit_subagent_start_calls_all_hooks(self):
        """Test that emit_subagent_start calls all registered hooks."""
        registry = HookRegistry()
        
        called = []
        
        class TestHook:
            def __init__(self, name):
                self.name = name
            
            def on_subagent_start(self, event):
                called.append(self.name)
        
        registry.register(TestHook("hook1"))
        registry.register(TestHook("hook2"))
        
        event = SubAgentStartEvent(
            parent_session_id="test",
            subagent_id="123",
            role_name="file-scout",
            task="test task",
        )
        
        registry.emit_subagent_start(event)
        
        assert called == ["hook1", "hook2"]

    def test_emit_swallows_hook_exceptions(self):
        """Test that hook exceptions don't crash the registry."""
        registry = HookRegistry()
        
        class BrokenHook:
            def on_subagent_start(self, event):
                raise RuntimeError("Hook failed!")
        
        registry.register(BrokenHook())
        
        event = SubAgentStartEvent(
            parent_session_id="test",
            subagent_id="123",
            role_name="file-scout",
            task="test task",
        )
        
        # Should not raise
        registry.emit_subagent_start(event)

    def test_get_hook_registry_returns_singleton(self):
        """Test that get_hook_registry returns the same instance."""
        registry1 = get_hook_registry()
        registry2 = get_hook_registry()
        
        assert registry1 is registry2


# ---------------------------------------------------------------------------
# LoggingHook Tests
# ---------------------------------------------------------------------------

class TestLoggingHook:
    """Tests for LoggingHook."""

    @patch("agent.observability.logging_hook.CustomLogger")
    def test_on_subagent_start_logs_event(self, mock_custom_logger_cls):
        """Test that on_subagent_start logs correctly."""
        mock_logger_instance = MagicMock()
        mock_custom_logger_cls.return_value = mock_logger_instance
        mock_logger_instance.debug = False
        
        hook = LoggingHook()
        
        event = SubAgentStartEvent(
            parent_session_id="parent-123",
            subagent_id="sub-456",
            role_name="file-scout",
            task="Search for files",
        )
        
        hook.on_subagent_start(event)
        
        # Verify info was called
        mock_logger_instance.info.assert_called_once()
        call_args = mock_logger_instance.info.call_args
        
        assert "Sub-agent started" in call_args[0][0]
        assert call_args[1]["event_type"] == "subagent_start"
        assert call_args[1]["parent_session"] == "parent-123"
        assert call_args[1]["subagent_id"] == "sub-456"
        assert call_args[1]["role"] == "file-scout"

    @patch("agent.observability.logging_hook.CustomLogger")
    def test_on_subagent_end_success_logs_info(self, mock_custom_logger_cls):
        """Test that successful sub-agent completion logs at INFO."""
        mock_logger_instance = MagicMock()
        mock_custom_logger_cls.return_value = mock_logger_instance
        mock_logger_instance.debug = False
        
        hook = LoggingHook()
        
        event = SubAgentEndEvent(
            parent_session_id="parent-123",
            subagent_id="sub-456",
            role_name="file-scout",
            success=True,
            result="Found 5 files",
            iterations=3,
            tools_used=["read_file", "glob"],
            duration_ms=123.45,
        )
        
        hook.on_subagent_end(event)
        
        # Verify log_event was called with INFO level
        mock_logger_instance.log_event.assert_called_once()
        call_args = mock_logger_instance.log_event.call_args
        
        assert call_args[0][0] == logging.INFO
        assert "completed" in call_args[0][1]
        assert call_args[1]["success"] is True
        assert call_args[1]["iterations"] == 3
        assert call_args[1]["duration_ms"] == 123.45

    @patch("agent.observability.logging_hook.CustomLogger")
    def test_on_subagent_end_failure_logs_warning(self, mock_custom_logger_cls):
        """Test that failed sub-agent completion logs at WARNING."""
        mock_logger_instance = MagicMock()
        mock_custom_logger_cls.return_value = mock_logger_instance
        mock_logger_instance.debug = False
        
        hook = LoggingHook()
        
        event = SubAgentEndEvent(
            parent_session_id="parent-123",
            subagent_id="sub-456",
            role_name="file-scout",
            success=False,
            result="Error: exceeded iterations",
            iterations=8,
            tools_used=["read_file"],
            duration_ms=500.0,
        )
        
        hook.on_subagent_end(event)
        
        # Verify log_event was called with WARNING level
        mock_logger_instance.log_event.assert_called_once()
        call_args = mock_logger_instance.log_event.call_args
        
        assert call_args[0][0] == logging.WARNING
        assert "failed" in call_args[0][1]
        assert call_args[1]["success"] is False

    @patch("agent.observability.logging_hook.CustomLogger")
    def test_on_tool_call_logs_event(self, mock_custom_logger_cls):
        """Test that on_tool_call logs correctly."""
        mock_logger_instance = MagicMock()
        mock_custom_logger_cls.return_value = mock_logger_instance
        mock_logger_instance.debug = False
        
        hook = LoggingHook()
        
        event = ToolCallEvent(
            session_id="session-123",
            tool_name="read_file",
            arguments={"path": "test.txt"},
            result="File contents",
            permission_outcome="allow",
            duration_ms=10.5,
        )
        
        hook.on_tool_call(event)
        
        # Verify info was called
        mock_logger_instance.info.assert_called_once()
        call_args = mock_logger_instance.info.call_args
        
        assert "Tool called" in call_args[0][0]
        assert call_args[1]["duration_ms"] == 10.5

        assert call_args[1]["tool"] == "read_file"
        assert call_args[1]["permission_outcome"] == "allow"

    @patch("agent.observability.logging_hook.CustomLogger")
    def test_on_llm_call_success_logs_info(self, mock_custom_logger_cls):
        mock_logger_instance = MagicMock()
        mock_custom_logger_cls.return_value = mock_logger_instance
        mock_logger_instance.debug = False
        
        """Test that successful LLM call logs at INFO."""
        hook = LoggingHook()
        
        event = LLMCallEvent(
            session_id="session-123",
            model="qwen-agentworld-35b-a3b",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            duration_ms=234.5,
            success=True,
        )
        
        hook.on_llm_call(event)
        
        mock_logger_instance.log_event.assert_called_once()
        call_args = mock_logger_instance.log_event.call_args
        
        assert call_args[0][0] == logging.INFO
        assert "completed" in call_args[0][1]
        assert call_args[1]["model"] == "qwen-agentworld-35b-a3b"
        assert call_args[1]["total_tokens"] == 150

    @patch("agent.observability.logging_hook.CustomLogger")
    def test_on_llm_call_failure_logs_warning(self, mock_custom_logger_cls):
        mock_logger_instance = MagicMock()
        mock_custom_logger_cls.return_value = mock_logger_instance
        mock_logger_instance.debug = False
        
        """Test that failed LLM call logs at WARNING."""
        hook = LoggingHook()
        
        event = LLMCallEvent(
            session_id="session-123",
            model="qwen-agentworld-35b-a3b",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            duration_ms=50.0,
            success=False,
        )
        
        hook.on_llm_call(event)
        
        mock_logger_instance.log_event.assert_called_once()
        call_args = mock_logger_instance.log_event.call_args
        
        assert call_args[0][0] == logging.WARNING
        assert "failed" in call_args[0][1]


# ---------------------------------------------------------------------------
# Event Tests
# ---------------------------------------------------------------------------

class TestSubAgentStartEvent:
    """Tests for SubAgentStartEvent."""

    def test_event_has_required_fields(self):
        """Test that event has all required fields."""
        event = SubAgentStartEvent(
            parent_session_id="parent-123",
            subagent_id="sub-456",
            role_name="file-scout",
            task="Search files",
        )
        
        assert event.parent_session_id == "parent-123"
        assert event.subagent_id == "sub-456"
        assert event.role_name == "file-scout"
        assert event.task == "Search files"
        assert event.timestamp is not None

    def test_event_timestamp_is_utc(self):
        """Test that timestamp is timezone-aware UTC."""
        
        event = SubAgentStartEvent(
            parent_session_id="test",
            subagent_id="test",
            role_name="test",
            task="test",
        )
        
        assert event.timestamp.tzinfo == UTC


class TestSubAgentEndEvent:
    """Tests for SubAgentEndEvent."""

    def test_event_has_required_fields(self):
        """Test that event has all required fields."""
        event = SubAgentEndEvent(
            parent_session_id="parent-123",
            subagent_id="sub-456",
            role_name="file-scout",
            success=True,
            result="Done",
            iterations=5,
            tools_used=["read_file", "grep"],
            duration_ms=123.45,
        )
        
        assert event.parent_session_id == "parent-123"
        assert event.subagent_id == "sub-456"
        assert event.role_name == "file-scout"
        assert event.success is True
        assert event.result == "Done"
        assert event.iterations == 5
        assert event.tools_used == ["read_file", "grep"]
        assert event.duration_ms == 123.45
        assert event.timestamp is not None
