"""Prometheus metrics for vio-gateway.

Exposed at GET /metrics. Designed to be scraped by Azure Monitor /
Prometheus agent.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# ─── Metric definitions ──────────────────────────────────────────────

ws_connections = Gauge(
    "vio_gateway_ws_connections",
    "Active WebSocket connections",
    labelnames=["session_id"],
)

ws_messages_sent = Counter(
    "vio_gateway_ws_messages_sent_total",
    "WebSocket messages sent to clients",
    labelnames=["session_id", "type"],
)

grpc_requests = Counter(
    "vio_gateway_grpc_requests_total",
    "gRPC RPC calls",
    labelnames=["rpc", "status"],
)

grpc_stream_frames = Counter(
    "vio_gateway_grpc_stream_frames_total",
    "MatchFrame messages emitted over gRPC streams",
    labelnames=["client"],
)

events_persisted = Counter(
    "vio_gateway_events_persisted_total",
    "Events written to Postgres",
    labelnames=["event_type"],
)

sessions_created = Counter(
    "vio_gateway_sessions_created_total",
    "Session rows created in Postgres",
)

http_request_duration = Histogram(
    "vio_gateway_http_request_duration_seconds",
    "REST API request latency",
    labelnames=["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


def render() -> bytes:
    """Return Prometheus exposition format text."""
    return generate_latest()
