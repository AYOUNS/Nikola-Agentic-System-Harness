"""Tests for agent.hooks.context and agent.hooks.instrumentation.

Covers:
- ContextVar isolation: parallel sub-agents must each see their own RunContext
  (asyncio copies the context at Task creation, so concurrent tasks are
  isolated without any extra effort — but if that ever breaks the metrics
  would silently mis-attribute events).
- classify_status: direct unit coverage for the status mapping logic.
- subagent_scope end event: the finally block must fire even when the body raises,
  so active_runs cannot leak.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.hooks.context import RunContext, current_context, use_context
from agent.hooks.instrumentation import classify_status, subagent_scope
from agent.hooks.types import HookRegistry, SubAgentEndEvent, SubAgentStartEvent

# ---------------------------------------------------------------------------
# classify_status
# ---------------------------------------------------------------------------

class TestClassifyStatus:

    def test_normal_answer_is_success(self):
        assert classify_status("Here is the result.", guard_fired=None) == "success"

    def test_error_prefix_is_error(self):
        assert classify_status("[Error: LLM call failed: timeout]", guard_fired=None) == "error"

    def test_guard_always_wins_over_answer(self):
        # Even an answer that starts with [Error should be "guard" when a guard fired
        assert classify_status("[Error: whatever]", guard_fired="max_iterations") == "guard"
        assert classify_status("fine answer", guard_fired="max_time") == "guard"

    def test_empty_answer_is_success(self):
        assert classify_status("", guard_fired=None) == "success"


# ---------------------------------------------------------------------------
# ContextVar isolation under asyncio.gather
# ---------------------------------------------------------------------------

class TestContextVarIsolation:
    """Each asyncio Task gets its own copy of the context at creation time.

    Two subagent_scope contexts run under asyncio.gather. Each one must see
    only its own RunContext — the one installed by its use_context call — and
    never the other's. The test also confirms that the ContextVar returns to
    its default value after each scope exits (use_context resets the token).
    """

    async def test_parallel_subagents_see_own_context(self):
        """Parallel tasks must not share each other's RunContext."""
        recorded: dict[str, str] = {}

        async def run_subagent(subagent_id: str, role: str) -> None:
            ctx = RunContext(
                session_id=subagent_id,
                agent=role,
                parent_session_id="parent",
            )
            with use_context(ctx):
                # Yield so the other task can run in between
                await asyncio.sleep(0)
                recorded[subagent_id] = current_context().session_id

        await asyncio.gather(
            run_subagent("sub-A", "file-scout"),
            run_subagent("sub-B", "scheduler"),
        )

        assert recorded["sub-A"] == "sub-A", (
            "sub-A read a context that did not belong to it"
        )
        assert recorded["sub-B"] == "sub-B", (
            "sub-B read a context that did not belong to it"
        )

    async def test_context_restored_after_scope_exits(self):
        """use_context must restore the previous context when the block exits."""
        outer = RunContext(session_id="outer", agent="main")
        inner = RunContext(session_id="inner", agent="sub")

        with use_context(outer):
            assert current_context().session_id == "outer"
            with use_context(inner):
                assert current_context().session_id == "inner"
            assert current_context().session_id == "outer"


# ---------------------------------------------------------------------------
# subagent_scope end event fires even when body raises
# ---------------------------------------------------------------------------

class TestSubagentScopeFinally:
    """The end event must be emitted even when the scope body raises.

    active_subagents is a gauge incremented on start and decremented on end;
    if the finally block were skipped the gauge would permanently read 1 after
    any sub-agent error, and the run could never appear idle again.
    """

    def test_end_event_emitted_on_exception(self):
        hooks = HookRegistry()
        events: list[object] = []
        hooks.register(type("R", (), {
            "on_subagent_start": staticmethod(lambda e: events.append(e)),
            "on_subagent_end": staticmethod(lambda e: events.append(e)),
        })())

        import agent.hooks.instrumentation as instr
        original = instr.get_hook_registry
        instr.get_hook_registry = lambda: hooks  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="boom"), subagent_scope("parent", "sub-1", "file-scout", "task"):
                raise RuntimeError("boom")
        finally:
            instr.get_hook_registry = original  # type: ignore[assignment]

        assert len(events) == 2
        assert isinstance(events[0], SubAgentStartEvent)
        end = events[1]
        assert isinstance(end, SubAgentEndEvent)
        assert end.success is False
        assert "RuntimeError" in end.result
