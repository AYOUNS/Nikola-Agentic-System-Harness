"""CLI entry-point for the agent harness.

Usage:
    python -m agent.cli
    python -m agent.cli --prompt "List the files in the workspace"
    python -m agent.cli --mode default --config config.yaml
    python -m agent.cli --prompt "List the files in the workspace"
    python -m agent.cli --mode default --config config.yaml
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

import typer

from agent.cli.completion import self_test
from agent.cli.repl import run_repl
from agent.cli.ui import ConsoleUI
from agent.config import load_app_config, to_dict, with_context_override, with_model_override
from agent.hooks.instrumentation import instrument_dispatch, instrument_stream_dispatch
from agent.llm.client import make_client
from agent.llm.dispatcher import (
    make_chat_completions_effect,
    make_chat_completions_stream_effect,
)
from agent.loop import make_process_turn_fn_v2
from agent.mcp.manager import MCPManager
from agent.tools import make_default_tool_registry
from agent.tools.filesystem import get_workspace_root

logger = logging.getLogger(__name__)

app = typer.Typer(add_completion=False)

_CONFIG_OPTION = typer.Option(
    Path("config.yaml"),
    "--config",
    "-c",
    help="Path to the YAML configuration file.",
    exists=True,
)

_MODEL_OPTION = typer.Option(
    None,
    "--model",
    "-m",
    help="Override the model from config.yaml (e.g. gemma4-31b-it).",
)

_PROMPT_OPTION = typer.Option(
    None,
    "--prompt",
    "-p",
    help="Execute a one-shot prompt and exit immediately.",
)

_MODE_OPTION = typer.Option(
    "default",
    "--mode",
    help="Agent mode (loads agent/prompts/<mode>.md).",
)

_STREAM_OPTION = typer.Option(
    True,
    "--stream/--no-stream",
    help="Render tokens as they arrive (default), or wait for each complete response.",
)

_DOCTOR_OPTION = typer.Option(
    False,
    "--doctor",
    help="Report why Tab completion is or is not working, then exit.",
)

_THINKING_OPTION = typer.Option(
    True,
    "--thinking/--no-thinking",
    help="Show the model's chain-of-thought beside the spinner while it thinks.",
)


_NOTICE_REASON_CHARS = 120


def mcp_failure_notice(failures: dict[str, str]) -> tuple[str, str]:
    """Build the "what happened, what to do" pair for MCP servers that never came up.

    A single failure names its reason inline, since that is usually the whole
    story — a missing command, a refused connection. Several reasons would
    overrun the box and bury the one that matters, so those are counted and
    left to /mcp, which lists them per server.

    failures: server name to failure reason, as MCPManager.failures returns it.

    Returns (what, todo), ready for ConsoleUI.notice.

    Example:
        >>> mcp_failure_notice({"github": "FileNotFoundError: npx"})[0]
        "MCP server 'github' did not connect (FileNotFoundError: npx)"
    """
    if len(failures) == 1:
        name, reason = next(iter(failures.items()))
        # "|" is the notice's own what/todo separator, so it cannot survive in a
        # reason that was written by someone else's exception.
        reason = reason.replace("|", "/")
        if len(reason) > _NOTICE_REASON_CHARS:
            reason = reason[: _NOTICE_REASON_CHARS - 1].rstrip() + "…"
        what = f"MCP server '{name}' did not connect ({reason})"
        todo = "Its tools are unavailable this session. Run /mcp for the full error."
    else:
        what = f"{len(failures)} MCP servers did not connect: {', '.join(sorted(failures))}"
        todo = "Their tools are unavailable this session. Run /mcp for the reason per server."

    return what, todo


def mcp_startup_notice(name: str, output: str) -> tuple[str, str]:
    """Build the "what happened, what to do" pair for a server's startup stderr.

    The text belongs to someone else's process, so it is flattened to one line
    and capped — a server that dumps a paragraph must not push the rest of the
    banner off screen. The full text is in the log either way.

    name:   Server identifier from mcp.json.
    output: Raw stderr the server wrote while starting up.

    Returns (what, todo), ready for ConsoleUI.notice.

    Example:
        >>> mcp_startup_notice("chrome", "exposes browser content")[0]
        "MCP server 'chrome' on startup: exposes browser content"
    """
    # "|" is the notice's own what/todo separator and this text is not ours.
    flattened = " ".join(output.split()).replace("|", "/")
    if len(flattened) > _NOTICE_REASON_CHARS:
        flattened = flattened[: _NOTICE_REASON_CHARS - 1].rstrip() + "…"

    what = f"MCP server '{name}' on startup: {flattened}"
    todo = "The server is connected and its tools are available. Full text is in the log."
    return what, todo


@app.command()
def main(
    config: Path = _CONFIG_OPTION,
    model: str | None = _MODEL_OPTION,
    prompt: str | None = _PROMPT_OPTION,
    mode: str = _MODE_OPTION,
    stream: bool = _STREAM_OPTION,
    thinking: bool = _THINKING_OPTION,
    doctor: bool = _DOCTOR_OPTION,
) -> None:
    """Start the agent harness in REPL or one-shot mode."""
    if doctor:
        # Runs before make_client so it works without a configured token.
        self_test()
        return

    # Run the async main in event loop
    asyncio.run(_async_main(config, model, prompt, mode, stream, thinking))


async def _async_main(
    config: Path,
    model: str | None,
    prompt: str | None,
    mode: str,
    stream: bool,
    thinking: bool,
) -> None:
    """Async entry point for the agent harness."""
    # Load typed config
    cfg = load_app_config(config)
    
    # Apply model override if provided
    if model is not None:
        cfg = with_model_override(cfg, model)

    # Configure structured JSON logging before anything that might log, and
    # exactly once — configuring twice reported a log-file fallback twice.
    import logging as _logging

    from agent.logging import configure_logging
    
    # Derive log level from config.yaml logging.debug setting
    _log_level = _logging.DEBUG if cfg.logging.debug else _logging.INFO

    # Logs are JSON lines on disk rather than on the terminal: interleaving
    # them with the transcript made both unreadable, and a file is what `jq`
    # wants anyway. Path resolution is pure, so it is safe this early.
    _log_path = get_workspace_root(cfg.workspace) / ".nikola" / "logs" / "agent.jsonl"
    
    session_id = str(uuid.uuid4())
    log_dest = configure_logging(
        level=_log_level, session_id=session_id, log_file=_log_path
    )

    # Initialize observability hooks
    from agent.hooks import get_hook_registry
    from agent.observability import LoggingHook
    
    hook_registry = get_hook_registry()
    hook_registry.register(LoggingHook(
        enabled=cfg.logging.enabled,
        debug=cfg.logging.debug,
    ))
    metrics_server = None
    if cfg.metrics.enabled:
        from agent.observability import PrometheusHook, start_metrics_server

        hook_registry.register(PrometheusHook())
        metrics_server = start_metrics_server(port=cfg.metrics.port, host=cfg.metrics.host)
    logger.info("Observability hooks initialized", extra={
        "logging_enabled": cfg.logging.enabled,
        "logging_debug": cfg.logging.debug,
        "metrics_enabled": cfg.metrics.enabled,
        "metrics_url": metrics_server.url if metrics_server else None,
    })

    logger.info("Session started", extra={"session_id": session_id, "model": cfg.llm.model})

    # Presented as a boxed error under the banner rather than printed here —
    # raw text above the logo is both ugly and easy to miss.
    log_notice = None
    if log_dest.fell_back:
        _what = f"Could not write logs to {log_dest.requested} ({log_dest.reason})"
        if log_dest.path is not None:
            _todo = f"Logging to {log_dest.path} instead."
        else:
            _todo = "Logging to stderr, so records will interleave with this transcript."
        log_notice = f"{_what}|{_todo}"

    # Convert to dict for legacy interfaces (make_client still expects dict)
    cfg_dict = to_dict(cfg)
    client = make_client(cfg_dict)

    # Create command registry first so UI can use it for completions
    from agent.cli.commands import make_default_command_registry
    commands = make_default_command_registry()

    ui = ConsoleUI(
        streams=stream,
        show_thinking=thinking,
        command_registry=commands,
        log_path=log_dest.path,
        log_notice=log_notice,
    )

    # Sub-agents report through the same hooks the logger uses, so the live
    # cards need no wiring beyond this registration.
    hook_registry.register(ui.subagents)

    # Get dynamic context configuration from model info
    from agent.config import ContextConfig as ContextConfigClass
    from agent.llm.models import get_context_config
    
    context_dict = get_context_config(client, cfg_dict)
    # Convert dict to typed ContextConfig and update main config
    context_config = ContextConfigClass(
        window_tokens=context_dict.get("window_tokens", cfg.context.window_tokens),
        reserve_output_tokens=context_dict.get("reserve_output_tokens", cfg.context.reserve_output_tokens),
        chars_per_token=context_dict.get("chars_per_token", cfg.context.chars_per_token),
        max_observation_chars=context_dict.get("max_observation_chars", cfg.context.max_observation_chars),
        summary_max_tokens=context_dict.get("summary_max_tokens", cfg.context.summary_max_tokens),
        summary_reasoning_effort=context_dict.get("summary_reasoning_effort", cfg.context.summary_reasoning_effort),
    )
    cfg = with_context_override(cfg, context_config)
    cfg_dict = to_dict(cfg)  # Update dict for legacy code

    # Get workspace root (needed for skills and MCP)
    workspace_root = get_workspace_root(cfg.workspace)
    
    # Initialize memory store for long-term persistence
    memory_store = None
    vector_store = None


    try:
        from agent.memory.store import MemoryStore
        from agent.memory.vectors import VectorStore, make_embedding_function
        
        # Store database in workspace/.nikola/.memory/memories.db
        memory_db_path = workspace_root / ".nikola" / ".memory" / "memories.db"
        memory_store = MemoryStore(memory_db_path)
        logger.info(f"Memory store initialized: {memory_db_path}")
        
        # Initialize vector store for semantic search
        try:
            embedding_fn = make_embedding_function(cfg_dict["llm"])
            qdrant_url = cfg.memory.qdrant_url
            collection_name = cfg.memory.qdrant_collection

            # qdrant_client warns through the warnings module when it cannot
            # reach the server, which lands as raw text above the banner.
            # Record it instead and say what it means in the box below.
            import warnings as _warnings
            with _warnings.catch_warnings(record=True) as caught:
                _warnings.simplefilter("always")
                vector_store = VectorStore(
                    qdrant_url=qdrant_url,
                    collection_name=collection_name,
                    embedding_fn=embedding_fn,
                    vector_size=4096,  # octen-embedding-8b produces 4096-dim vectors
                )
                available = vector_store.is_available()

            for warning in caught:
                logger.warning(f"{warning.category.__name__}: {warning.message}")

            if available:
                logger.info(f"Vector store initialized: {qdrant_url}")
                # Memories written during an earlier outage are still unembedded. Indexing
                # them now is what makes the embedded flag a recovery path rather than a
                # record of a gap that never closes.
                from agent.memory import backfill_embeddings

                backfilled = backfill_embeddings(memory_store, vector_store)
                if backfilled:
                    logger.info(f"Backfilled {backfilled} embedding(s) after vector store startup")
            else:
                logger.warning("Vector store unavailable - will use keyword search fallback")
                ui.notice(
                    f"Semantic memory search is off (no vector store at {qdrant_url})",
                    "Memory falls back to keyword search. Start Qdrant to re-enable it.",
                )
        except Exception as e:
            logger.warning(f"Vector store initialization failed: {e} - will use keyword search fallback")
            vector_store = None
            ui.notice(
                f"Vector store could not start ({type(e).__name__}: {e})",
                "Memory falls back to keyword search.",
            )
    except Exception as e:
        logger.warning(f"Memory initialization failed: {e}")
        # Continue without memory - session still works with other tools
    
    # Create tool registry with workspace, skills, and memory
    loaded_skills: set[str] = set()
    registry = make_default_tool_registry(
        workspace=str(workspace_root),
        loaded_skills=loaded_skills,
        memory_store=memory_store,
        vector_store=vector_store,
        session_id=session_id,
    )

    # Initialize MCP Manager
    mcp_manager = MCPManager(str(workspace_root))
    
    # Connect to MCP servers and register their tools
    try:
        await mcp_manager.connect_all(registry)
        server_count = len(mcp_manager.clients)
        if server_count > 0:
            logger.info(f"Connected to {server_count} MCP server(s)")
        # connect_all keeps going past a server that fails, which is right — but
        # it leaves the terminal looking like every server came up. Say so.
        if mcp_manager.failures:
            ui.notice(*mcp_failure_notice(mcp_manager.failures))
        # A server that printed a warning on startup said it for a reason, and
        # its stderr no longer reaches the terminal on its own.
        for _name, _output in sorted(mcp_manager.startup_output.items()):
            ui.notice(*mcp_startup_notice(_name, _output))
    except Exception as e:
        logger.warning(f"MCP initialization failed: {e}")
        # Continue without MCP - session still works with built-in tools
        ui.notice(
            f"MCP could not be initialized ({type(e).__name__}: {e})",
            "The session continues with built-in tools only.",
        )

    # Build one instrumented dispatch pair — shared by the loop, compaction and delegate tools
    dispatch = instrument_dispatch(
        make_chat_completions_effect(client, cfg_dict), model=cfg.llm.model,
    )
    stream_dispatch = (
        instrument_stream_dispatch(
            make_chat_completions_stream_effect(client, cfg_dict), model=cfg.llm.model,
        )
        if stream else None
    )

    # Register delegate tools for sub-agent delegation
    from agent.subagents.orchestrator import make_delegate_parallel_tool, make_delegate_tool

    # Sequential delegation
    _delegate = make_delegate_tool(
        parent_session_id=session_id,
        registry=registry,
        llm_dispatcher=dispatch,
        workspace=str(workspace_root),
    )
    registry.register(
        name=_delegate["name"],
        fn=_delegate["fn"],
        schema=_delegate["schema"],
    )

    # Parallel delegation
    _delegate_parallel = make_delegate_parallel_tool(
        parent_session_id=session_id,
        registry=registry,
        llm_dispatcher=dispatch,
        workspace=str(workspace_root),
    )
    registry.register(
        name=_delegate_parallel["name"],
        fn=_delegate_parallel["fn"],
        schema=_delegate_parallel["schema"],
    )

    # Create permission module
    from agent.permissions import PermissionModule

    permission_module = PermissionModule(is_subagent=False)

    # Use new engine-based loop (eliminates 150+ lines of duplication)
    process_turn_fn = make_process_turn_fn_v2(
        dispatch,
        registry,
        cfg_dict,
        stream_dispatch=stream_dispatch,
        on_output=ui.sink if stream else None,
        permission_module=permission_module,
        confirmation_fn=ui.confirm_tool_use,  # UI handles user confirmation
        session_id=session_id,
        memory_store=memory_store,
        vector_store=vector_store,
    )

    try:
        await run_repl(
            process_turn_fn,
            one_shot_prompt=prompt,
            mode=mode,
            ui=ui,
            model=cfg.llm.model,
            tools=registry,
            commands=commands,  # Pass commands so REPL can use them
            mcp_manager=mcp_manager,
            config=cfg_dict,
            loaded_skills=loaded_skills,
            memory_store=memory_store,
            vector_store=vector_store,
            permission_module=permission_module,  # Pass permission module for /permissions command
            session_id=session_id,
        )
    finally:
        # Cleanup MCP connections on exit
        await mcp_manager.disconnect_all()
        # Close Qdrant client before Python tears down the import system,
        # preventing the __del__-triggered "sys.meta_path is None" log noise.
        if vector_store is not None:
            vector_store.close()


if __name__ == "__main__":
    app()
