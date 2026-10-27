"""Structured JSON logging for the agent harness.

Provides a JSON log formatter and a one-call setup function.

Every log record emitted after configure_logging() is called is written as a
single-line JSON object — to a rotating file when log_file is given, and to
stderr otherwise — with the fields:

    ts        ISO-8601 UTC timestamp
    level     Log level string (INFO, WARNING, ERROR, …)
    logger    Logger name (dotted module path)
    msg       Formatted log message
    session   Session ID injected via configure_logging()  (optional)
    run       Run/trace ID — set per-turn via set_run_id()  (optional)
    **extra   Any extra fields passed via logger.info(..., extra={...})

Usage
-----
    from agent.logging import configure_logging, set_run_id

    configure_logging(
        level=logging.INFO,
        session_id="abc-123",
        log_file="workspace/.nikola/logs/agent.jsonl",
    )

    # At the start of each ReAct turn:
    set_run_id("turn-007")

    # Anywhere:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("tool dispatched", extra={"tool": "read_file"})

    # Output (single line, prettified here for readability):
    {
      "ts": "2025-01-01T12:00:00.000Z",
      "level": "INFO",
      "logger": "agent.loop",
      "session": "abc-123",
      "run": "turn-007",
      "msg": "tool dispatched",
      "tool": "read_file"
    }

Thread/task safety
------------------
set_run_id() stores the current run ID in a threading.local so concurrent
coroutines on different OS threads don't overwrite each other.  Within a
single-threaded asyncio event loop all coroutines share the same thread;
set_run_id() should therefore be called once per turn before dispatching
tool calls.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Module-level storage for per-session and per-run IDs
_session_id: str | None = None
_local = threading.local()

# Third-party loggers that narrate every HTTP request at INFO. Their output
# duplicates what the transcript already shows and drowns everything else, so
# they are clamped to WARNING unless the harness is explicitly in debug mode.
_NOISY_LOGGERS: tuple[str, ...] = ("httpx", "httpcore", "openai", "urllib3")

# Keep a bounded amount of history on disk rather than growing without limit.
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 3

# Keys that are part of LogRecord's standard attributes — we skip them in
# the **extra expansion so we don't duplicate fields already in the schema.
_SKIP_ATTRS: frozenset[str] = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


def set_run_id(run_id: str | None) -> None:
    """Set the run/trace ID for the current thread.

    run_id: Identifier for the current ReAct turn or sub-agent run.
            Pass None to clear.

    Example:
        set_run_id(f"turn-{iteration}")
        logger.info("starting LLM call")
        # -> {"run": "turn-3", ...}
    """
    _local.run_id = run_id


def get_run_id() -> str | None:
    """Return the run/trace ID for the current thread, or None."""
    return getattr(_local, "run_id", None)


class _JsonFormatter(logging.Formatter):
    """Format each LogRecord as a single-line JSON string.

    Fields emitted:
        ts        ISO-8601 UTC timestamp with millisecond precision.
        level     Log level name.
        logger    Logger name.
        session   Session ID (from configure_logging).
        run       Per-turn run ID (from set_run_id).
        msg       Formatted message string.
        exc       Exception traceback string (only when an exception is attached).
        **extra   Any extra key=value pairs passed by the caller.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise record to a JSON line.

        record: The LogRecord to format.

        Returns a single-line JSON string (no trailing newline).
        """
        record.message = record.getMessage()

        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
        }

        if _session_id:
            doc["session"] = _session_id

        run = get_run_id()
        if run:
            doc["run"] = run

        doc["msg"] = record.message

        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        # Append caller-supplied extra fields (skip standard LogRecord attrs)
        for key, value in record.__dict__.items():
            if key not in _SKIP_ATTRS and not key.startswith("_"):
                doc[key] = value

        return json.dumps(doc, default=str)


def _is_ours(handler: logging.Handler) -> bool:
    """True when this handler is one configure_logging installed."""
    return isinstance(getattr(handler, "formatter", None), _JsonFormatter)


@dataclass(frozen=True)
class LogDestination:
    """Where log records ended up, and what went wrong on the way.

    Returned rather than printed so the caller decides how to present a
    fallback — the CLI renders it as a boxed error once the terminal UI
    exists, instead of writing raw text above the banner.

    path:      File being written to, or None when logging to a stream.
    requested: File that was asked for, or None when none was.
    reason:    Why `requested` could not be used, or None when it was.
    """

    path: Path | None
    requested: Path | None = None
    reason: str | None = None

    @property
    def fell_back(self) -> bool:
        """True when records are not going where the caller asked."""
        return self.reason is not None


def configure_logging(
    level: int = logging.WARNING,
    session_id: str | None = None,
    stream=None,
    log_file: str | Path | None = None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    backup_count: int = _DEFAULT_BACKUP_COUNT,
) -> LogDestination:
    """Install the JSON formatter on the root logger.

    Idempotent — calling multiple times replaces any previously installed
    handler rather than adding duplicate handlers.

    When log_file is given the records go to that file instead of the
    terminal, one JSON object per line, so the transcript stays readable and
    the log stays machine-readable:

        jq 'select(.level == "ERROR")' agent.jsonl
        tail -f agent.jsonl | jq .

    The file rotates at max_bytes to keep history bounded. If it cannot be
    opened — a read-only workspace, for instance — a file under the system
    temp directory is used instead, and only if that also fails do records go
    to the stream. They are never silently dropped.

    level:        Minimum log level to emit (e.g. logging.INFO).
    session_id:   Stable identifier for this agent session (injected into
                  every log record for correlation across turns).
    stream:       Output stream used when no log_file is given, or when the
                  log file cannot be opened; defaults to sys.stderr.
    log_file:     Path to a JSON-lines log file. None keeps the stream.
    max_bytes:    Rotate once the file reaches this size.
    backup_count: How many rotated files to keep.

    Returns a LogDestination describing where records actually go and, when
    the requested file could not be used, why. Nothing is printed — the caller
    presents a fallback in whatever way suits its interface.

    Example:
        configure_logging(level=logging.INFO, session_id="abc-123",
                          log_file="workspace/.nikola/logs/agent.jsonl")
    """
    global _session_id
    _session_id = session_id

    root = logging.getLogger()

    # Drop the handler from any previous call, closing its file descriptor.
    for existing in [h for h in root.handlers if _is_ours(h)]:
        root.removeHandler(existing)
        existing.close()

    handler: logging.Handler | None = None
    resolved: Path | None = None
    primary: Path | None = None
    reason: str | None = None

    if log_file is not None:
        # The workspace is read-only in some deployments, so a temp directory
        # is tried before giving up on a file entirely — falling straight back
        # to stderr would put the JSON back in the transcript, which is the
        # thing the file sink exists to prevent.
        primary = Path(log_file).expanduser()
        candidates = [primary, Path(tempfile.gettempdir()) / "nikola" / primary.name]

        for candidate in candidates:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    candidate,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
            except OSError as err:
                if reason is None:  # keep the first failure — it names the real cause
                    # strerror is the bare cause ("Read-only file system");
                    # str(err) repeats the errno and path the caller already has.
                    reason = err.strerror or str(err)
                continue
            resolved = candidate
            break

    if handler is None:
        handler = logging.StreamHandler(stream or sys.stderr)

    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)

    # Clamp the HTTP client chatter unless we are explicitly debugging.
    noisy_level = level if level <= logging.DEBUG else logging.WARNING
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(noisy_level)

    return LogDestination(path=resolved, requested=primary, reason=reason)
