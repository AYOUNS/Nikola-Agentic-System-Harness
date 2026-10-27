"""Ambient context for the run that lifecycle events belong to.

Every event carries the session it came from, but the code that emits one — a
dispatch callable, a memory store, a tool adapter — sits far below the place
that knows the id. Threading a parameter through all of those signatures is
what produced the ``session_id="main-session"`` placeholders; a ContextVar
carries it instead.

Task-local by construction: asyncio copies the current context when it creates
a Task, so parallel sub-agents each see their own RunContext and never each
other's, and a scope that exits restores whatever was there before it.

See also: agent.hooks.instrumentation, which is what sets it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RunContext:
    """Who is running, for events emitted anywhere beneath them.

    Attributes:
        session_id: Session the events belong to — the agent's session for a
            top-level run, the sub-agent's own id inside a sub-agent
        agent: Agent label ("main", or the sub-agent's role name)
        run_id: Unique id of the run in progress, when inside one
        parent_session_id: Session that started this one, for sub-agents
    """

    session_id: str
    agent: str = "main"
    run_id: str | None = None
    parent_session_id: str | None = None

    @property
    def mode(self) -> str:
        """Which side of the delegation boundary this run is on.

        "subagent" inside a delegated run, "agent" at the top level. Derived
        from parent_session_id rather than stored, so it cannot drift from the
        context subagent_scope installs — only a sub-agent has a parent.
        """
        return "subagent" if self.parent_session_id is not None else "agent"


_UNSET = RunContext(session_id="unset")
_current: ContextVar[RunContext] = ContextVar("agent_run_context", default=_UNSET)


def current_context() -> RunContext:
    """The context of the run in progress, or a placeholder outside one."""
    return _current.get()


def current_session_id() -> str | None:
    """The session id of the run in progress, or None outside one."""
    ctx = _current.get()
    return None if ctx is _UNSET else ctx.session_id


@contextmanager
def use_context(ctx: RunContext) -> Iterator[RunContext]:
    """Install ``ctx`` for the duration of the block, then restore the previous one.

    Example:
        with use_context(RunContext(session_id="abc", agent="main")):
            ...  # events emitted in here report session "abc"
    """
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
