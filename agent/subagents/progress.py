"""Live counters for a running sub-agent.

A sub-agent is opaque while it runs: the parent blocks inside one tool call and
the terminal has nothing to show until a result comes back. This module keeps
the running totals — tool calls and tokens — and pushes them out as
SubAgentProgressEvents so a display can render them as they accumulate.

Nothing here knows about terminals. The orchestrator owns a SubAgentProgress
per run; agent.cli.subagent_view turns the events into cards.

See also: agent.subagents.orchestrator.execute_subagent, agent.hooks.types.
"""

from __future__ import annotations

from typing import Any

from agent.hooks import SubAgentProgressEvent, get_hook_registry


class SubAgentProgress:
    """Running totals for one sub-agent, emitted as they change.

    The orchestrator feeds it two things: each LLM response (for tokens) and
    each batch of tool calls (for the call count). Every update emits a
    SubAgentProgressEvent, so a subscriber never has to poll.

    Example:
        progress = SubAgentProgress("parent-1", "ab12ef", "file-scout")
        progress.record_tool_calls(["read_file", "grep"])
        progress.record_response(completion)
    """

    def __init__(
        self,
        parent_session_id: str,
        subagent_id: str,
        role_name: str,
    ) -> None:
        """Start counters at zero for one sub-agent run.

        parent_session_id: parent's session ID, for correlation.
        subagent_id:       this run's short ID.
        role_name:         role being run.
        """
        self.parent_session_id = parent_session_id
        self.subagent_id = subagent_id
        self.role_name = role_name

        self.tool_calls = 0
        self.total_tokens = 0
        self.current_tool: str | None = None
        # Tool names in call order, deduplicated — what the end event reports.
        self.tools_used: list[str] = []

    def record_tool_calls(self, tool_names: list[str]) -> None:
        """Count a batch of tool calls about to be dispatched."""
        if not tool_names:
            return
        self.tool_calls += len(tool_names)
        self.current_tool = tool_names[-1]
        for name in tool_names:
            if name not in self.tools_used:
                self.tools_used.append(name)
        self.emit()

    def record_response(self, response: Any) -> None:
        """Add one LLM response's usage to the totals.

        A response without a usage block (a streamed one, or a test double)
        contributes nothing rather than raising — the counters are a display,
        and a missing count must not break the run.
        """
        usage = getattr(response, "usage", None)
        total = getattr(usage, "total_tokens", None)
        if total is None:
            prompt = getattr(usage, "prompt_tokens", None) or 0
            completion = getattr(usage, "completion_tokens", None) or 0
            total = prompt + completion

        try:
            self.total_tokens += int(total)
        except (TypeError, ValueError):
            pass

        # The model is thinking again, not running a tool.
        self.current_tool = None
        self.emit()

    def emit(self) -> None:
        """Publish the current totals."""
        get_hook_registry().emit_subagent_progress(
            SubAgentProgressEvent(
                parent_session_id=self.parent_session_id,
                subagent_id=self.subagent_id,
                role_name=self.role_name,
                tool_calls=self.tool_calls,
                total_tokens=self.total_tokens,
                current_tool=self.current_tool,
            )
        )
