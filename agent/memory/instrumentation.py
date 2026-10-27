"""Lifecycle-hook instrumentation for memory operations.

Wraps a store operation in a timer and emits a MemoryOperationEvent to the
global hook registry, so metrics and logs stay decoupled from storage code.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from agent.hooks.context import current_session_id
from agent.hooks.types import MemoryOperationEvent, get_hook_registry


@dataclass
class MemoryOperation:
    """Mutable outcome of an in-flight operation, filled in by the caller."""
    success: bool = True
    results: int | None = None
    session_id: str | None = None


@contextmanager
def memory_operation(operation: str, backend: str) -> Iterator[MemoryOperation]:
    """Time an operation and emit its event on exit.

    Callers that swallow their own errors set ``op.success = False``; an
    exception escaping the block is recorded as a failure and re-raised.

    Example:
        with memory_operation("search", "sqlite") as op:
            rows = ...
            op.results = len(rows)
    """
    op = MemoryOperation()
    start = time.perf_counter()
    try:
        yield op
    except Exception:
        op.success = False
        raise
    finally:
        get_hook_registry().emit_memory_operation(MemoryOperationEvent(
            operation=operation,
            backend=backend,
            success=op.success,
            duration_ms=(time.perf_counter() - start) * 1000,
            results=op.results,
            session_id=op.session_id or current_session_id(),
        ))
