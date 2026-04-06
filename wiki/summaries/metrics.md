# metrics.py

**Source:** `src/spicebridge/metrics.py`

## Purpose

Persistent metrics collector for server monitoring. Tracks per-tool call counts, success/error/latency, time-bucketed history (hourly/daily), high water marks, and system metrics via psutil.

## Public API

- **`ServerMetrics`**: Main class. Thread-safe.
  - `record_request(tool_name)`: Records incoming tool call, updates RPM tracking.
  - `record_success(tool_name, duration_ms)` / `record_error(tool_name, duration_ms, error_msg)`: Records outcome with latency.
  - `record_sim_start()` / `record_sim_end(duration_ms)`: Active simulation gauge.
  - `record_rejection()`: Tracks throttled requests.
  - `check_rpm()`: Returns True if under RPM limit.
  - `snapshot()`: Returns full metrics dict for `/health` endpoint.
  - `set_circuit_counter(fn)`: Stores callable for live circuit count.
  - `start_persistence()`: Starts background daemon thread saving to disk every 60s.
  - `shutdown()`: Stops persistence thread, does final save.
  - `save()`: Atomic write to `~/.spicebridge/metrics.json`.

## Data Structures

- **`ToolStats`** dataclass: Per-tool `calls`, `successes`, `errors`, `latency_sum_ms`, `last_called`.
- **`TimeBucket`** dataclass: Ring-buffer element for hourly (24 slots) and daily (7 slots) history.
- **`_PersistenceThread`**: Daemon thread that calls `save()` every 60 seconds.

## System Metrics

Collects via `psutil` (cached for 60s): CPU percent, RAM usage, disk usage, process-specific CPU/RAM.

## Persistence

Metrics survive server restarts via JSON file at `~/.spicebridge/metrics.json`. Atomic write using tmp file + `os.replace`. File permissions set to 0o600.

## Dependencies

`psutil` (optional), `threading`, `json`, `collections.deque`.

## Architecture Role

Observability layer. Instantiated in [server.py](server.md) as `_metrics`. Powers the `/health` endpoint and the `_monitored` decorator. See [observability](../concepts/observability.md).
