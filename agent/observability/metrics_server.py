"""HTTP scrape endpoint for Prometheus.

Serves the prometheus_client registry on ``http://<host>:<port>/metrics`` from a
daemon thread, so it lives exactly as long as the agent process and never blocks
the REPL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Thread
from wsgiref.simple_server import WSGIServer

from prometheus_client import REGISTRY, CollectorRegistry, start_http_server

logger = logging.getLogger(__name__)


@dataclass
class MetricsServer:
    """Handle to a running metrics endpoint."""
    server: WSGIServer
    thread: Thread
    host: str
    port: int

    @property
    def url(self) -> str:
        """Scrape URL of this endpoint."""
        return f"http://{self.host}:{self.port}/metrics"

    def stop(self) -> None:
        """Shut the endpoint down and wait for its thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_metrics_server(
    port: int = 9464,
    host: str = "0.0.0.0",  # reachable from the Prometheus container
    registry: CollectorRegistry = REGISTRY,
) -> MetricsServer | None:
    """Start serving ``/metrics`` in a background thread.

    A port that is already taken (a second agent on the same host, say) must not
    stop the agent from starting, so bind failures are logged and return None.

    Args:
        port: TCP port to listen on. 0 picks a free port.
        host: Interface to bind.
        registry: Collector registry to expose.

    Returns:
        MetricsServer handle, or None if the port could not be bound.
    """
    try:
        server, thread = start_http_server(port, addr=host, registry=registry)
    except OSError as err:
        logger.warning(
            "Metrics endpoint not started",
            extra={"event_type": "metrics_server_error", "host": host, "port": port, "error": str(err)},
        )
        return None

    bound_port = server.server_port
    logger.info(
        "Metrics endpoint started",
        extra={"event_type": "metrics_server_started", "host": host, "port": bound_port},
    )
    return MetricsServer(server=server, thread=thread, host=host, port=bound_port)
