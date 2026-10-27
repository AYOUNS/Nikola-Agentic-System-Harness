"""
mitmproxy addon: SSE streaming + HAR file only.

Features:
- Captures streaming responses directly to HAR file
- Multi-proxy safe with separate HAR files per proxy
"""
import json
import logging

from mitmproxy import http, ctx
from mitmproxy.addons.savehar import SaveHar

# Configure logging
logger = logging.getLogger(__name__)


class SSEStreamingHAR:
    """
    Addon that captures SSE/streaming responses and writes only to HAR file.
    """

    def __init__(self) -> None:
        self.har_addon = SaveHar()

    def load(self, loader) -> None:
        """Register options and disable web UI password prompt."""
        # Set empty argon2 hash programmatically to bypass web UI password prompt without committing secrets
        ctx.options.web_password = "$argon2i$v=19$m=8,t=1,p=1$YWFhYWFhYWE$nXD9kg"
        loader.add_option(
            name="redact_secrets",
            typespec=bool,
            default=True,
            help=(
                "Redact sensitive headers (Authorization, Cookie, Session, "
                "API keys, etc.) before writing them to the HAR file. "
                "Disable with --set redact_secrets=false."
            ),
        )

    # Header names (case-insensitive) that carry credentials/secrets and must
    # never be written to the HAR file.
    SENSITIVE_REQUEST_HEADERS = {
        "authorization",
        "proxy-authorization",
        "cookie",
        "x-api-key",
        "api-key",
        "x-auth-token",
        "x-session-token",
        "x-csrf-token",
        "session",
    }
    SENSITIVE_RESPONSE_HEADERS = {
        "set-cookie",
    }

    def _redact_sensitive_headers(self, flow: http.HTTPFlow) -> None:
        """Redact secret headers (auth tokens, session/cookies, API keys) before
        the flow is written to the HAR file, unless disabled via
        --set redact_secrets=false.
        """
        if not ctx.options.redact_secrets:
            return

        for name in self.SENSITIVE_REQUEST_HEADERS:
            if name in flow.request.headers:
                flow.request.headers[name] = "[REDACTED]"

        if flow.response:
            for name in self.SENSITIVE_RESPONSE_HEADERS:
                if name in flow.response.headers:
                    flow.response.headers[name] = "[REDACTED]"

    def request(self, flow: http.HTTPFlow) -> None:
        """Detect SSE requests by headers or JSON body."""
        try:
            # Check Accept header for SSE
            accept_header = flow.request.headers.get("Accept", "")
            if "text/event-stream" in accept_header:
                flow.metadata["is_sse"] = True
                return

            # Check if JSON request has stream=true
            content_type = flow.request.headers.get("Content-Type", "")
            if "application/json" in content_type and flow.request.content:
                try:
                    body = json.loads(flow.request.content)
                    if body.get("stream") is True:
                        flow.metadata["is_sse"] = True
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.debug(f"[SSE] Failed to parse JSON body: {e}")
        except Exception as e:
            logger.error(f"[SSE] Error in request hook: {e}", exc_info=True)

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        """Set up streaming capture when response headers indicate SSE."""
        try:
            if not flow.response:
                return

            content_type = flow.response.headers.get("Content-Type", "")
            if "text/event-stream" in content_type or flow.metadata.get("is_sse"):
                ctx.log.info("[SSE] Capturing streaming response")
                flow.metadata["sse_chunks"] = []  # Store chunks in memory only

                def stream_and_capture(data: bytes) -> bytes:
                    """Callback for each chunk of streamed data."""
                    try:
                        if data:
                            # Store in memory only (no file writing)
                            chunks: list[bytes] = flow.metadata.get("sse_chunks", [])
                            chunks.append(data)
                        return data
                    except Exception as e:
                        logger.error(f"[SSE] Error capturing chunk: {e}", exc_info=True)
                        return data  # Always return data to avoid breaking the stream

                flow.response.stream = stream_and_capture
        except Exception as e:
            logger.error(f"[SSE] Error in responseheaders hook: {e}", exc_info=True)

    def response(self, flow: http.HTTPFlow) -> None:
        """Handle completed responses - update HAR with full content."""
        try:
            # If this was a streamed response, reconstruct the content
            chunks: list[bytes] = flow.metadata.get("sse_chunks", [])
            if chunks and flow.response:
                try:
                    full_content = b"".join(chunks)
                    flow.response.content = full_content
                    ctx.log.info(f"[SSE] Captured {len(full_content)} bytes from {len(chunks)} chunks")
                except Exception as e:
                    logger.error(f"[SSE] Error reconstructing content: {e}", exc_info=True)

            # Add flow to memory and update HAR
            try:
                self._redact_sensitive_headers(flow)
                self.har_addon.response(flow)
            except Exception as e:
                logger.error(f"[SSE] Error writing to HAR: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"[SSE] Error in response hook: {e}", exc_info=True)

    def done(self) -> None:
        """Finalize HAR file."""
        try:
            self.har_addon.done()
            ctx.log.info("[SSE] Final HAR written successfully")
        except Exception as e:
            logger.error(f"[SSE] Error finalizing HAR: {e}", exc_info=True)

    def error(self, flow: http.HTTPFlow) -> None:
        """Handle flow errors gracefully and save partial content."""
        try:
            error_msg = flow.error.msg if flow.error else "Unknown error"
            ctx.log.warn(f"[SSE] Flow error for {flow.request.url}: {error_msg}")

            # Reconstruct any partial content from chunks before saving
            chunks: list[bytes] = flow.metadata.get("sse_chunks", [])
            if chunks and flow.response:
                try:
                    full_content = b"".join(chunks)
                    flow.response.content = full_content
                    ctx.log.info(f"[SSE] Saved partial content: {len(full_content)} bytes from {len(chunks)} chunks before disconnect")
                except Exception as e:
                    logger.error(f"[SSE] Error reconstructing partial content: {e}", exc_info=True)

            # Save to HAR even with error (captures partial data)
            if flow.response:
                try:
                    self._redact_sensitive_headers(flow)
                    self.har_addon.response(flow)
                    ctx.log.info("[SSE] Partial response saved to HAR despite error")
                except Exception as e:
                    logger.error(f"[SSE] Failed to save partial response: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"[SSE] Error in error hook: {e}", exc_info=True)


addons = [SSEStreamingHAR()]
