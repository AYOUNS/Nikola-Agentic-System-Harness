"""Integration tests for the short-term → long-term memory promotion path.

These tests exist because unit coverage of the summarizer proved only that the function
works, never that anything calls it: tier 3 stayed unwired behind five passing unit tests.
Each test here therefore asserts on the *wiring* — that a budget-triggered compaction, run
through the real engine, leaves both a recap in the window and a row in SQLite.

No real LLM and no Qdrant: the dispatcher is a mock that answers a summarization request
differently from a turn request, and the vector store is a fake that records its calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.chat.chat_completion import Choice

from agent.context import find_summary_index
from agent.loop import make_process_turn_fn_v2
from agent.memory import backfill_embeddings, persist_summary_to_memory
from agent.memory.store import Memory, MemoryStore
from agent.tools import ToolRegistry

SUMMARY_TEXT = "Earlier: edited config.yaml, then ran the test suite and it passed."

# A window this small forces compaction on the first iteration; the reserve leaves a
# 200-token budget for the history, which the seeded conversation far exceeds.
TIGHT_CONTEXT_CONFIG = {
    "loop": {"max_iterations": 3},
    "context": {
        "window_tokens": 700,
        "reserve_output_tokens": 500,
        "max_observation_chars": 200,
    },
}


def _completion(content: str) -> ChatCompletion:
    """A finished assistant answer with no tool calls."""
    return ChatCompletion(
        id="mock",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content, tool_calls=None),
            )
        ],
        created=0,
        model="mock",
        object="chat.completion",
    )


def _two_faced_dispatch(calls: list[dict]):
    """Return a dispatcher that answers summarization and turn requests differently.

    A summarization request carries no tools — it is a plain completion over a rendered
    transcript — which is what distinguishes it from the engine's own call.
    """

    def dispatch(request: dict) -> ChatCompletion:
        calls.append(request)
        if "tools" not in request:
            return _completion(SUMMARY_TEXT)
        return _completion("Done.")

    return dispatch


def _long_conversation(turns: int = 6) -> list[ChatCompletionMessageParam]:
    """A conversation well over any small budget, in droppable user/assistant pairs."""
    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content="You are a helpful agent.")
    ]
    for index in range(turns):
        messages.append(
            ChatCompletionUserMessageParam(role="user", content=f"Task {index}: " + "x" * 600)
        )
        messages.append({"role": "assistant", "content": f"Result {index}: " + "y" * 600})
    messages.append(ChatCompletionUserMessageParam(role="user", content="What did we do?"))
    return messages


class FakeVectorStore:
    """Records upserts and deletes so wiring can be asserted without Qdrant."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.upserted: list[tuple[int, str]] = []
        self.deleted: list[int] = []
        self.calls: list[tuple[str, int]] = []  # interleaved, so ordering can be asserted

    def is_available(self) -> bool:
        return self.available

    def upsert(self, memory_id: int, content: str) -> bool:
        self.upserted.append((memory_id, content))
        self.calls.append(("upsert", memory_id))
        return True

    def delete(self, memory_id: int) -> bool:
        self.deleted.append(memory_id)
        self.calls.append(("delete", memory_id))
        return True


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    memory_store = MemoryStore(tmp_path / "memories.db")
    yield memory_store
    memory_store.close()


# ---------------------------------------------------------------------------
# 1. Budget-triggered compaction summarizes and promotes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_compaction_inserts_recap_in_window(store: MemoryStore) -> None:
    """Tier 3 runs on the loop's own compaction, not only on /compact."""
    calls: list[dict] = []
    process_turn = make_process_turn_fn_v2(
        _two_faced_dispatch(calls),
        ToolRegistry(),
        TIGHT_CONTEXT_CONFIG,
        session_id="session-a",
        memory_store=store,
    )

    messages = _long_conversation()
    await process_turn(messages)

    assert find_summary_index(messages) is not None, (
        "a budget-triggered compaction must leave a recap of the turns it dropped"
    )
    assert any("tools" not in call for call in calls), "the summarizer was never called"


@pytest.mark.asyncio
async def test_budget_compaction_promotes_summary_to_sqlite(store: MemoryStore) -> None:
    """The recap is also durable: this is the cross-session continuity guarantee."""
    process_turn = make_process_turn_fn_v2(
        _two_faced_dispatch([]),
        ToolRegistry(),
        TIGHT_CONTEXT_CONFIG,
        session_id="session-a",
        memory_store=store,
    )

    await process_turn(_long_conversation())

    promoted = store.search_keyword("config.yaml", limit=5)
    assert promoted, "the summary was never written to long-term memory"
    assert promoted[0].kind == "summary"
    assert promoted[0].source == "compaction"
    assert promoted[0].session_id == "session-a"


@pytest.mark.asyncio
async def test_promoted_summary_is_indexed_when_vectors_available(store: MemoryStore) -> None:
    """A promoted summary reaches the vector index, like an explicitly remembered fact."""
    vectors = FakeVectorStore()
    process_turn = make_process_turn_fn_v2(
        _two_faced_dispatch([]),
        ToolRegistry(),
        TIGHT_CONTEXT_CONFIG,
        session_id="session-a",
        memory_store=store,
        vector_store=vectors,
    )

    await process_turn(_long_conversation())

    assert vectors.upserted, "the promoted summary was never embedded"
    memory_id, content = vectors.upserted[-1]
    assert SUMMARY_TEXT in content
    assert store.get_by_id(memory_id).embedded is True


@pytest.mark.asyncio
async def test_compaction_without_memory_store_still_recaps(tmp_path: Path) -> None:
    """Memory is optional; losing it must cost the promotion, never the recap."""
    messages = _long_conversation()
    process_turn = make_process_turn_fn_v2(
        _two_faced_dispatch([]),
        ToolRegistry(),
        TIGHT_CONTEXT_CONFIG,
        session_id="session-a",
        memory_store=None,
    )

    await process_turn(messages)

    assert find_summary_index(messages) is not None


@pytest.mark.asyncio
async def test_summarizer_failure_does_not_break_the_turn(store: MemoryStore) -> None:
    """A failed summary is a lost recap; an exception into the engine is a lost turn."""

    def dispatch(request: dict) -> ChatCompletion:
        if "tools" not in request:
            raise RuntimeError("summarizer exploded")
        return _completion("Done anyway.")

    process_turn = make_process_turn_fn_v2(
        dispatch,
        ToolRegistry(),
        TIGHT_CONTEXT_CONFIG,
        session_id="session-a",
        memory_store=store,
    )

    answer = await process_turn(_long_conversation())

    assert answer == "Done anyway."
    assert store.get_stats()["summary_count"] == 0


# ---------------------------------------------------------------------------
# 2. Stale vectors are evicted when a summary is replaced
# ---------------------------------------------------------------------------

def test_replacing_a_summary_evicts_its_old_vector(store: MemoryStore) -> None:
    """Without this, the deleted row's vector keeps winning a top-k slot forever."""
    vectors = FakeVectorStore()

    persist_summary_to_memory("first recap", "session-a", store, vectors)
    first_id = vectors.upserted[0][0]

    persist_summary_to_memory("second recap", "session-a", store, vectors)

    assert first_id in vectors.deleted, "the replaced summary's vector was left orphaned"
    assert store.get_stats()["summary_count"] == 1
    assert store.search_keyword("second recap", limit=5)[0].content == "second recap"


def test_stale_vector_is_deleted_before_the_new_one_is_written(store: MemoryStore) -> None:
    """Ordering is load-bearing because SQLite reuses the deleted row's id.

    An INTEGER PRIMARY KEY takes the largest unused rowid, so the replacement summary is
    routinely assigned the id just freed. Evicting after the upsert would therefore delete
    the vector that was only just written, leaving the surviving row unindexed.
    """
    vectors = FakeVectorStore()

    persist_summary_to_memory("first recap", "session-a", store, vectors)
    first_id = vectors.upserted[0][0]
    persist_summary_to_memory("second recap", "session-a", store, vectors)
    second_id, second_content = vectors.upserted[-1]

    assert second_id == first_id, "precondition: this store reuses the freed rowid"
    assert vectors.calls == [
        ("upsert", first_id),
        ("delete", first_id),
        ("upsert", second_id),
    ], "the stale vector must be evicted before the replacement is indexed"
    assert second_content == "second recap"


def test_replacing_a_summary_leaves_other_sessions_indexed(store: MemoryStore) -> None:
    """Eviction is scoped to the session being replaced."""
    vectors = FakeVectorStore()

    persist_summary_to_memory("session A recap", "session-a", store, vectors)
    other_id = vectors.upserted[0][0]
    persist_summary_to_memory("session B recap", "session-b", store, vectors)
    persist_summary_to_memory("session B recap v2", "session-b", store, vectors)

    assert other_id not in vectors.deleted
    assert store.get_by_id(other_id) is not None


def test_persist_summary_survives_an_unavailable_vector_store(store: MemoryStore) -> None:
    """SQLite is the source of truth: an index outage must not cost the write."""
    vectors = FakeVectorStore(available=False)

    persist_summary_to_memory("recap", "session-a", store, vectors)

    assert store.get_stats()["summary_count"] == 1
    assert vectors.upserted == []


# ---------------------------------------------------------------------------
# 3. Backfill closes the gap left by an outage
# ---------------------------------------------------------------------------

def test_backfill_indexes_memories_written_during_an_outage(store: MemoryStore) -> None:
    """The embedded flag is a recovery path, so something must act on it."""
    written = store.write(
        Memory(
            content="Qdrant was down when this was stored",
            kind="fact",
            source="user",
            session_id="session-a",
        )
    )
    assert written.embedded is False

    vectors = FakeVectorStore()
    indexed = backfill_embeddings(store, vectors)

    assert indexed == 1
    assert vectors.upserted == [(written.id, written.content)]
    assert store.get_by_id(written.id).embedded is True


def test_backfill_is_a_noop_when_nothing_is_pending(store: MemoryStore) -> None:
    vectors = FakeVectorStore()

    assert backfill_embeddings(store, vectors) == 0
    assert vectors.upserted == []


def test_backfill_without_a_vector_store_reports_nothing_indexed(store: MemoryStore) -> None:
    store.write(Memory(content="a fact", kind="fact", source="user", session_id="session-a"))

    assert backfill_embeddings(store, None) == 0
