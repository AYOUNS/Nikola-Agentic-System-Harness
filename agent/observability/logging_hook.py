"""Logging-based observability hook with custom formatting.

Emits structured logs for all lifecycle events using the agent.logging infrastructure.
Can be used for debugging and as a foundation for metrics collection.

Supports configurable log levels:
- INFO: Basic event logging (default)
- DEBUG: Detailed entry/exit logging with full event data
"""

from __future__ import annotations

import logging
from typing import Any

from agent.hooks.types import (
    LLMCallEvent,
    SubAgentEndEvent,
    SubAgentStartEvent,
    ToolCallEvent,
)


class CustomLogger:
    """Custom logger wrapper with entry/exit tracking and debug control.
    
    Provides convenience methods for common logging patterns:
    - entry/exit logging
    - event logging with structured data
    - configurable enable/disable
    """
    
    def __init__(self, name: str, enabled: bool = True, debug: bool = False) -> None:
        """Initialize custom logger.
        
        Args:
            name: Logger name (module path)
            enabled: Whether logging is enabled
            debug: Whether debug mode is enabled
        """
        self.logger = logging.getLogger(name)
        self.enabled = enabled
        self.debug = debug
    
    def log_entry(self, method: str, **context: Any) -> None:
        """Log method entry (debug level only).
        
        Args:
            method: Method name
            **context: Additional context fields
        """
        if not self.enabled or not self.debug:
            return
        
        self.logger.debug(
            f"→ ENTRY: {method}",
            extra={
                "event_type": "hook_entry",
                "hook_method": method,
                **context,
            },
        )
    
    def log_exit(self, method: str, **context: Any) -> None:
        """Log method exit (debug level only).
        
        Args:
            method: Method name
            **context: Additional context fields
        """
        if not self.enabled or not self.debug:
            return
        
        self.logger.debug(
            f"← EXIT: {method}",
            extra={
                "event_type": "hook_exit",
                "hook_method": method,
                **context,
            },
        )
    
    def log_event(self, level: int, message: str, **extra: Any) -> None:
        """Log an event with structured data.
        
        Args:
            level: Log level (logging.INFO, logging.WARNING, etc.)
            message: Log message
            **extra: Additional structured fields
        """
        if not self.enabled:
            return
        
        self.logger.log(level, message, extra=extra)
    
    def info(self, message: str, **extra: Any) -> None:
        """Log info level message."""
        self.log_event(logging.INFO, message, **extra)
    
    def warning(self, message: str, **extra: Any) -> None:
        """Log warning level message."""
        self.log_event(logging.WARNING, message, **extra)
    
    def debug_msg(self, message: str, **extra: Any) -> None:
        """Log debug level message."""
        if self.enabled and self.debug:
            self.log_event(logging.DEBUG, message, **extra)


class LoggingHook:
    """Observability hook that emits structured logs with custom formatting.
    
    All events are logged with extra fields for filtering and analysis:
    - jq 'select(.event_type == "subagent_start")' agent.jsonl
    - jq 'select(.subagent_id)' agent.jsonl | jq -s 'group_by(.subagent_id)'
    
    Args:
        enabled: Whether to enable logging. Defaults to True.
        debug: Whether to enable detailed debug-level logging. Defaults to False.
    
    Example:
        # Basic logging (INFO level)
        hook = LoggingHook()
        
        # Detailed logging (DEBUG level)
        hook = LoggingHook(debug=True)
        
        # Disabled
        hook = LoggingHook(enabled=False)
    """
    
    def __init__(self, enabled: bool = True, debug: bool = False) -> None:
        """Initialize the logging hook.
        
        Args:
            enabled: Whether to enable logging
            debug: Whether to enable debug-level logging
        """
        self.logger = CustomLogger(__name__, enabled=enabled, debug=debug)
        
        if enabled and debug:
            self.logger.debug_msg("LoggingHook initialized with debug mode enabled")
        elif enabled:
            self.logger.debug_msg("LoggingHook initialized with info mode enabled")
        else:
            self.logger.debug_msg("LoggingHook initialized but disabled")
    
    def on_subagent_start(self, event: SubAgentStartEvent) -> None:
        """Log sub-agent start event.
        
        Args:
            event: Sub-agent start event
        """
        self.logger.log_entry(
            "on_subagent_start",
            subagent_id=event.subagent_id,
            role=event.role_name,
        )
        
        task_text = event.task if self.logger.debug else event.task[:200]
        
        self.logger.info(
            "Sub-agent started",
            event_type="subagent_start",
            parent_session=event.parent_session_id,
            subagent_id=event.subagent_id,
            role=event.role_name,
            task=task_text,
        )
        
        if self.logger.debug:
            self.logger.debug_msg(
                f"Sub-agent {event.subagent_id} delegated task",
                full_task=event.task,
                task_length=len(event.task),
            )
        
        self.logger.log_exit(
            "on_subagent_start",
            subagent_id=event.subagent_id,
        )
    
    def on_subagent_end(self, event: SubAgentEndEvent) -> None:
        """Log sub-agent end event.
        
        Args:
            event: Sub-agent end event
        """
        self.logger.log_entry(
            "on_subagent_end",
            subagent_id=event.subagent_id,
            success=event.success,
        )
        
        level = logging.INFO if event.success else logging.WARNING
        message = "Sub-agent completed" if event.success else "Sub-agent failed"
        
        self.logger.log_event(
            level,
            message,
            event_type="subagent_end",
            parent_session=event.parent_session_id,
            subagent_id=event.subagent_id,
            role=event.role_name,
            success=event.success,
            iterations=event.iterations,
            tools_used=event.tools_used,
            duration_ms=event.duration_ms,
            result_length=len(event.result),
        )
        
        if self.logger.debug:
            self.logger.debug_msg(
                f"Sub-agent {event.subagent_id} execution details",
                result_preview=event.result[:500] if event.result else None,
                tools_list=event.tools_used,
                efficiency_score=event.iterations / max(len(event.tools_used), 1),
            )
        
        self.logger.log_exit(
            "on_subagent_end",
            subagent_id=event.subagent_id,
            duration_ms=event.duration_ms,
        )
    
    def on_tool_call(self, event: ToolCallEvent) -> None:
        """Log tool call event.
        
        Args:
            event: Tool call event
        """
        self.logger.log_entry(
            "on_tool_call",
            tool=event.tool_name,
            session=event.session_id,
        )
        
        self.logger.info(
            "Tool called",
            event_type="tool_call",
            session=event.session_id,
            tool=event.tool_name,
            permission_outcome=event.permission_outcome,
            duration_ms=event.duration_ms,
        )
        
        if self.logger.debug:
            self.logger.debug_msg(
                f"Tool '{event.tool_name}' execution completed",
                permission=event.permission_outcome,
                latency_ms=event.duration_ms,
                session=event.session_id,
            )
        
        self.logger.log_exit(
            "on_tool_call",
            tool=event.tool_name,
            duration_ms=event.duration_ms,
        )
    
    def on_llm_call(self, event: LLMCallEvent) -> None:
        """Log LLM call event.
        
        Args:
            event: LLM call event
        """
        self.logger.log_entry(
            "on_llm_call",
            model=event.model,
            session=event.session_id,
        )
        
        level = logging.INFO if event.success else logging.WARNING
        message = "LLM call completed" if event.success else "LLM call failed"
        
        self.logger.log_event(
            level,
            message,
            event_type="llm_call",
            session=event.session_id,
            model=event.model,
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            total_tokens=event.total_tokens,
            duration_ms=event.duration_ms,
            success=event.success,
        )
        
        if self.logger.debug:
            # Calculate tokens per second for performance analysis
            tokens_per_sec = None
            if event.duration_ms and event.duration_ms > 0 and event.total_tokens:
                tokens_per_sec = round(event.total_tokens / (event.duration_ms / 1000), 2)
            
            # Calculate cost estimate (rough approximation)
            cost_estimate = None
            if event.prompt_tokens and event.completion_tokens:
                # Rough estimate: $0.002 per 1K tokens (adjust per model)
                cost_estimate = round(
                    (event.prompt_tokens + event.completion_tokens) / 1000 * 0.002,
                    6,
                )
            
            self.logger.debug_msg(
                f"LLM '{event.model}' performance metrics",
                tokens_per_second=tokens_per_sec,
                estimated_cost_usd=cost_estimate,
                token_ratio=round(event.completion_tokens / max(event.prompt_tokens, 1), 3)
                if event.prompt_tokens and event.completion_tokens
                else None,
            )
        
        self.logger.log_exit(
            "on_llm_call",
            model=event.model,
            success=event.success,
            total_tokens=event.total_tokens,
        )
