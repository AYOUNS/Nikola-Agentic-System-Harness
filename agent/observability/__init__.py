"""Observability implementations for agent monitoring (Week 3).

Provides hook implementations that consume lifecycle events:
- LoggingHook: Structured JSON logs with entry/exit tracking
- CustomLogger: Utility logger with debug control
- PrometheusHook: Prometheus counters, gauges and histograms
- start_metrics_server: HTTP /metrics endpoint for Prometheus scraping

Example usage:
    from agent.hooks import get_hook_registry
    from agent.observability import LoggingHook
    
    registry = get_hook_registry()
    
    # Basic logging
    registry.register(LoggingHook())
    
    # Debug mode with entry/exit tracking
    registry.register(LoggingHook(debug=True))
    
    # Disabled
    registry.register(LoggingHook(enabled=False))
    
    # Prometheus metrics on http://localhost:9464/metrics
    registry.register(PrometheusHook())
    start_metrics_server(port=9464)
"""

from agent.observability.logging_hook import CustomLogger, LoggingHook
from agent.observability.metrics_server import MetricsServer, start_metrics_server
from agent.observability.prometheus_hook import PrometheusHook

__all__ = [
    "CustomLogger",
    "LoggingHook",
    "MetricsServer",
    "PrometheusHook",
    "start_metrics_server",
]
