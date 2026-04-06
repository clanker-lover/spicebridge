# Observability

Cross-cutting concept appearing in: `metrics.py`, `server.py`, `__main__.py`

## FACTS

SPICEBridge has a comprehensive observability stack built around the `ServerMetrics` class (Source: `metrics.py`):

### Metrics Collection
- **Per-tool stats**: Call count, success/error count, cumulative latency, last-called timestamp for every tool.
- **Rolling windows**: Request timestamps in a deque for 1-minute and 5-minute request counts.
- **Simulation gauge**: Active simulation count, duration tracking (last 100).
- **Throttle tracking**: Rejected request count and timestamps.
- **Time buckets**: Hourly (24 slots) and daily (7 slots) ring buffers with total/errors/latency.
- **High water marks**: Peak concurrent sims, peak RPM, peak requests/hour.
- **System metrics**: CPU, RAM, disk via psutil (cached 60s).

### Health Endpoint
`GET /health?token=<SPICEBRIDGE_HEALTH_TOKEN>` returns a JSON snapshot of all metrics (Source: `server.py`). Protected by a separate token (not the API key). Returns 404 if token is unset or wrong -- never reveals the endpoint exists.

### Persistence
Metrics survive server restarts via `~/.spicebridge/metrics.json`. A daemon thread saves every 60 seconds. Final save on shutdown via atexit handler. Atomic writes using tmp + `os.replace`.

### Rate Limiting
`check_rpm()` enforces `SPICEBRIDGE_MAX_RPM` (default 60) by counting requests in the last 60 seconds. Only enforced when `_http_transport` is True (Source: `server.py`).

## INFERENCES

The dual-token design (API key for tools, health token for monitoring) allows separate access control -- an ops dashboard can monitor without having tool access. The 404-on-bad-token pattern prevents endpoint enumeration.

## Related Pages

- [metrics.py](../summaries/metrics.md), [server.py](../summaries/server.md)
- [mcp-tool-architecture](mcp-tool-architecture.md) -- `_monitored` decorator
