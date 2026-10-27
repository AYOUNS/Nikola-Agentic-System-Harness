"""Tests for agent/logging.py — JSON formatter, configure_logging, set_run_id."""

from __future__ import annotations

import io
import json
import logging

import pytest

from agent.logging import configure_logging, get_run_id, set_run_id

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stream_logger(name: str = "test") -> tuple[logging.Logger, io.StringIO]:
    """Return a (logger, buffer) pair wired with the JSON formatter."""
    buf = io.StringIO()
    configure_logging(level=logging.DEBUG, session_id="test-session", stream=buf)
    return logging.getLogger(name), buf


def _last_record(buf: io.StringIO) -> dict:
    """Parse the last JSON line written to *buf*."""
    buf.seek(0)
    lines = [l for l in buf.read().splitlines() if l.strip()]
    assert lines, "No log output was produced"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# _JsonFormatter output shape
# ---------------------------------------------------------------------------

class TestJsonFormatterFields:
    def test_ts_field_present(self):
        logger, buf = _make_stream_logger()
        logger.info("hello")
        rec = _last_record(buf)
        assert "ts" in rec
        assert rec["ts"].endswith("Z")

    def test_level_field(self):
        logger, buf = _make_stream_logger()
        logger.warning("watch out")
        rec = _last_record(buf)
        assert rec["level"] == "WARNING"

    def test_logger_name(self):
        logger, buf = _make_stream_logger("agent.tools")
        logger.info("dispatched")
        rec = _last_record(buf)
        assert rec["logger"] == "agent.tools"

    def test_msg_field(self):
        logger, buf = _make_stream_logger()
        logger.info("tool dispatched")
        rec = _last_record(buf)
        assert rec["msg"] == "tool dispatched"

    def test_session_injected(self):
        buf = io.StringIO()
        configure_logging(level=logging.DEBUG, session_id="sess-xyz", stream=buf)
        logging.getLogger("test.session").info("event")
        rec = _last_record(buf)
        assert rec["session"] == "sess-xyz"

    def test_no_session_when_none(self):
        buf = io.StringIO()
        configure_logging(level=logging.DEBUG, session_id=None, stream=buf)
        logging.getLogger("test.nosession").info("event")
        rec = _last_record(buf)
        assert "session" not in rec

    def test_extra_fields_included(self):
        logger, buf = _make_stream_logger()
        logger.info("event", extra={"tool": "read_file", "duration_ms": 42})
        rec = _last_record(buf)
        assert rec["tool"] == "read_file"
        assert rec["duration_ms"] == 42

    def test_exc_field_on_exception(self):
        logger, buf = _make_stream_logger()
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("caught")
        rec = _last_record(buf)
        assert "exc" in rec
        assert "ValueError" in rec["exc"]

    def test_output_is_valid_json_per_line(self):
        logger, buf = _make_stream_logger()
        for i in range(3):
            logger.info(f"msg {i}")
        buf.seek(0)
        lines = [l for l in buf.read().splitlines() if l.strip()]
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # must not raise


# ---------------------------------------------------------------------------
# set_run_id / get_run_id
# ---------------------------------------------------------------------------

class TestRunId:
    def setup_method(self):
        set_run_id(None)

    def test_default_is_none(self):
        assert get_run_id() is None

    def test_set_and_get(self):
        set_run_id("turn-007")
        assert get_run_id() == "turn-007"

    def test_clear_with_none(self):
        set_run_id("turn-1")
        set_run_id(None)
        assert get_run_id() is None

    def test_run_id_in_log_record(self):
        buf = io.StringIO()
        configure_logging(level=logging.DEBUG, session_id=None, stream=buf)
        set_run_id("turn-42")
        logging.getLogger("test.run").info("iteration start")
        rec = _last_record(buf)
        assert rec["run"] == "turn-42"

    def test_no_run_field_when_not_set(self):
        set_run_id(None)
        buf = io.StringIO()
        configure_logging(level=logging.DEBUG, session_id=None, stream=buf)
        logging.getLogger("test.norun").info("event")
        rec = _last_record(buf)
        assert "run" not in rec


# ---------------------------------------------------------------------------
# configure_logging idempotency
# ---------------------------------------------------------------------------

class TestConfigureLoggingIdempotency:
    def test_second_call_does_not_duplicate_handlers(self):
        buf = io.StringIO()
        configure_logging(level=logging.DEBUG, session_id="s1", stream=buf)
        configure_logging(level=logging.DEBUG, session_id="s1", stream=buf)
        logging.getLogger("test.idem").info("only once")
        buf.seek(0)
        lines = [l for l in buf.read().splitlines() if l.strip()]
        # Should appear exactly once despite two configure calls
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# File sink — logs live on disk as JSON lines, not in the transcript
# ---------------------------------------------------------------------------

class TestFileSink:
    def test_writes_json_lines_to_the_file(self, tmp_path):
        target = tmp_path / "logs" / "agent.jsonl"
        dest = configure_logging(level=logging.INFO, session_id="s1", log_file=target)

        logging.getLogger("agent.loop").info("tool dispatched", extra={"tool": "read_file"})

        assert dest.path == target
        assert not dest.fell_back
        rec = json.loads(target.read_text().strip())
        assert rec["msg"] == "tool dispatched"
        assert rec["tool"] == "read_file"
        assert rec["session"] == "s1"

    def test_creates_missing_parent_directories(self, tmp_path):
        target = tmp_path / "deeply" / "nested" / "agent.jsonl"
        configure_logging(level=logging.INFO, session_id=None, log_file=target)
        logging.getLogger("agent.loop").info("event")
        assert target.is_file()

    def test_every_line_parses_independently(self, tmp_path):
        """jq reads one object per line, so no record may span lines."""
        target = tmp_path / "agent.jsonl"
        configure_logging(level=logging.INFO, session_id=None, log_file=target)

        logging.getLogger("agent.loop").info("first\nsecond")  # embedded newline
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("agent.loop").exception("failed")

        lines = [l for l in target.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must not raise

    def test_traceback_goes_to_the_file_not_the_stream(self, tmp_path):
        target = tmp_path / "agent.jsonl"
        buf = io.StringIO()
        configure_logging(level=logging.INFO, session_id=None, log_file=target, stream=buf)

        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("agent.tools").exception("dispatch failed")

        rec = json.loads(target.read_text().strip())
        assert "Traceback" in rec["exc"]
        assert buf.getvalue() == ""

    def test_rotation_keeps_history_bounded(self, tmp_path):
        target = tmp_path / "agent.jsonl"
        configure_logging(
            level=logging.INFO, session_id=None, log_file=target,
            max_bytes=2000, backup_count=2,
        )
        for i in range(400):
            logging.getLogger("agent.loop").info("x" * 80, extra={"i": i})

        assert {p.name for p in tmp_path.iterdir()} == {
            "agent.jsonl", "agent.jsonl.1", "agent.jsonl.2",
        }

    @pytest.mark.skipif(
        __import__("os").getuid() == 0,
        reason="root bypasses filesystem permissions — fallback path untestable",
    )
    def test_unwritable_path_falls_back_to_a_temp_file(self, tmp_path, capsys):
        """A read-only workspace must not push the JSON back into the transcript."""
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o500)
        buf = io.StringIO()
        wanted = readonly / "sub" / "fallback-probe.jsonl"

        dest = configure_logging(
            level=logging.INFO, session_id=None, log_file=wanted, stream=buf,
        )

        logging.getLogger("agent.loop").info("still logging")

        assert dest.fell_back, "the caller must be told the request was not honoured"
        assert dest.requested == wanted
        assert dest.path is not None and dest.path != wanted
        assert dest.path.name == wanted.name
        assert "Read-only" in dest.reason or "Permission denied" in dest.reason
        assert "still logging" in dest.path.read_text(), "records must not be dropped"
        assert buf.getvalue() == "", "the transcript stream must stay clean"
        assert capsys.readouterr().err == "", "configure_logging must not print"
        dest.path.unlink()

    def test_stream_still_used_when_no_file_given(self, tmp_path):
        buf = io.StringIO()
        dest = configure_logging(level=logging.INFO, session_id=None, stream=buf)
        logging.getLogger("agent.loop").info("event")
        assert dest.path is None
        assert not dest.fell_back
        assert "event" in buf.getvalue()


# ---------------------------------------------------------------------------
# HTTP client chatter
# ---------------------------------------------------------------------------

class TestNoisyLoggers:
    def test_httpx_info_is_suppressed_at_info_level(self):
        buf = io.StringIO()
        configure_logging(level=logging.INFO, session_id=None, stream=buf)
        logging.getLogger("httpx").info('HTTP Request: POST /chat "HTTP/1.1 200 OK"')
        assert buf.getvalue() == ""

    def test_httpx_warnings_still_get_through(self):
        buf = io.StringIO()
        configure_logging(level=logging.INFO, session_id=None, stream=buf)
        logging.getLogger("httpx").warning("retrying upstream")
        assert "retrying upstream" in buf.getvalue()

    def test_agent_loggers_keep_info(self):
        buf = io.StringIO()
        configure_logging(level=logging.INFO, session_id=None, stream=buf)
        logging.getLogger("agent.loop").info("still visible")
        assert "still visible" in buf.getvalue()

    def test_debug_mode_lets_httpx_through(self):
        buf = io.StringIO()
        configure_logging(level=logging.DEBUG, session_id=None, stream=buf)
        logging.getLogger("httpx").info("HTTP Request: POST /chat")
        assert "HTTP Request" in buf.getvalue()
