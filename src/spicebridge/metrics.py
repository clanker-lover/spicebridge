"""Persistent metrics collector for SPICEBridge.

Tracks per-tool call counts, success/error/latency, time-bucketed history,
high water marks, and system metrics via psutil.  All state persists across
restarts via a JSON file written by a background daemon thread.

All methods are thread-safe.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_DEFAULT_PERSIST_PATH = Path.home() / ".spicebridge" / "metrics.json"


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

@dataclass
class ToolStats:
    """Per-tool rich tracking."""

    calls: int = 0
    successes: int = 0
    errors: int = 0
    latency_sum_ms: float = 0.0
    last_called: str | None = None


@dataclass
class TimeBucket:
    """Auto-resetting time bucket indexed by epoch period."""

    epoch_period: int = 0
    total: int = 0
    errors: int = 0
    latency_sum_ms: float = 0.0


# ------------------------------------------------------------------
# Persistence thread
# ------------------------------------------------------------------

class _PersistenceThread(threading.Thread):
    """Daemon thread that periodically saves metrics to disk."""

    def __init__(self, metrics: ServerMetrics, interval: float = 60.0) -> None:
        super().__init__(daemon=True, name="metrics-persist")
        self._metrics = metrics
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            try:
                self._metrics.save()
            except Exception:
                logger.debug("Metrics save failed", exc_info=True)

    def stop(self) -> None:
        self._stop_event.set()


# ------------------------------------------------------------------
# ServerMetrics
# ------------------------------------------------------------------

class ServerMetrics:
    """Collects request counts, simulation timing, throttle rejections,
    per-tool stats, time-bucketed history, and system metrics."""

    def __init__(
        self,
        max_rpm: int = 60,
        persist_path: Path | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._start_wall = time.time()

        # Per-tool request counters (total since startup) — backward compat
        self._tool_counts: dict[str, int] = defaultdict(int)

        # Rolling window of request timestamps (for 1m / 5m counts)
        self._request_times: deque[float] = deque()

        # Active simulation gauge
        self._active_sims = 0

        # Simulation durations (last 100)
        self._sim_durations: deque[float] = deque(maxlen=100)

        # Throttle rejection tracking
        self._rejected_total = 0
        self._rejected_times: deque[float] = deque()

        # RPM limit
        self._max_rpm = max_rpm

        # --- New persistent state ---
        self._tool_stats: dict[str, ToolStats] = defaultdict(ToolStats)
        self._hourly_buckets: list[TimeBucket] = [TimeBucket() for _ in range(24)]
        self._daily_buckets: list[TimeBucket] = [TimeBucket() for _ in range(7)]
        self._error_log: deque[dict] = deque(maxlen=50)
        self._peak_concurrent_sims: int = 0
        self._peak_rpm: int = 0
        self._peak_requests_per_hour: int = 0
        self._cumulative_uptime_s: float = 0.0
        self._last_request_ts: str | None = None

        # --- Runtime-only state ---
        self._circuit_count_fn: Callable[[], int] | None = None
        self._system_metrics: dict | None = None
        self._system_metrics_ts: float = 0.0
        self._persist_thread: _PersistenceThread | None = None

        # Persistence
        if persist_path is None:
            self._persist_path: Path = _DEFAULT_PERSIST_PATH
        else:
            self._persist_path = persist_path

        self._load()

    # ------------------------------------------------------------------
    # Recording (original methods — backward compat preserved)
    # ------------------------------------------------------------------

    def record_request(self, tool_name: str) -> None:
        """Record an incoming tool call."""
        now = time.monotonic()
        iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            self._tool_counts[tool_name] += 1
            self._request_times.append(now)
            self._last_request_ts = iso_now

            # Rich per-tool tracking
            stats = self._tool_stats[tool_name]
            stats.calls += 1
            stats.last_called = iso_now

            # Update time buckets
            self._update_buckets(0, 0.0)

            # Update peak RPM
            cutoff_1m = now - 60
            rpm = sum(1 for t in self._request_times if t >= cutoff_1m)
            if rpm > self._peak_rpm:
                self._peak_rpm = rpm

            # Update peak requests per hour
            epoch_hour = int(time.time() // 3600)
            h_idx = epoch_hour % 24
            bucket = self._hourly_buckets[h_idx]
            if bucket.epoch_period == epoch_hour:
                if bucket.total > self._peak_requests_per_hour:
                    self._peak_requests_per_hour = bucket.total

    def record_success(self, tool_name: str, duration_ms: float) -> None:
        """Record a successful tool call with latency."""
        with self._lock:
            stats = self._tool_stats[tool_name]
            stats.successes += 1
            stats.latency_sum_ms += duration_ms
            self._update_buckets(0, duration_ms)

    def record_error(self, tool_name: str, duration_ms: float, error_msg: str) -> None:
        """Record a failed tool call with latency and error message."""
        iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            stats = self._tool_stats[tool_name]
            stats.errors += 1
            stats.latency_sum_ms += duration_ms
            self._update_buckets(1, duration_ms)

            # Append to error log (truncate message at 200 chars)
            self._error_log.append({
                "timestamp": iso_now,
                "tool": tool_name,
                "message": error_msg[:200],
            })

    def record_sim_start(self) -> None:
        """Increment active simulation gauge."""
        with self._lock:
            self._active_sims += 1
            if self._active_sims > self._peak_concurrent_sims:
                self._peak_concurrent_sims = self._active_sims

    def record_sim_end(self, duration_ms: float) -> None:
        """Decrement active simulation gauge and record duration."""
        with self._lock:
            self._active_sims = max(0, self._active_sims - 1)
            self._sim_durations.append(duration_ms)

    def record_rejection(self) -> None:
        """Record a throttled/rejected request."""
        now = time.monotonic()
        with self._lock:
            self._rejected_total += 1
            self._rejected_times.append(now)

    # ------------------------------------------------------------------
    # Time bucket helpers (must be called with lock held)
    # ------------------------------------------------------------------

    def _update_buckets(self, error_count: int, latency_ms: float) -> None:
        """Update hourly and daily buckets. Must be called with lock held."""
        now_epoch = time.time()
        epoch_hour = int(now_epoch // 3600)
        epoch_day = int(now_epoch // 86400)

        h_idx = epoch_hour % 24
        h_bucket = self._hourly_buckets[h_idx]
        if h_bucket.epoch_period != epoch_hour:
            # New hour — reset bucket
            h_bucket.epoch_period = epoch_hour
            h_bucket.total = 0
            h_bucket.errors = 0
            h_bucket.latency_sum_ms = 0.0
        h_bucket.total += 1
        h_bucket.errors += error_count
        h_bucket.latency_sum_ms += latency_ms

        d_idx = epoch_day % 7
        d_bucket = self._daily_buckets[d_idx]
        if d_bucket.epoch_period != epoch_day:
            d_bucket.epoch_period = epoch_day
            d_bucket.total = 0
            d_bucket.errors = 0
            d_bucket.latency_sum_ms = 0.0
        d_bucket.total += 1
        d_bucket.errors += error_count
        d_bucket.latency_sum_ms += latency_ms

    # ------------------------------------------------------------------
    # Throttle checks
    # ------------------------------------------------------------------

    def check_rpm(self) -> bool:
        """Return True if under RPM limit, False if over."""
        now = time.monotonic()
        cutoff = now - 60
        with self._lock:
            self._prune_deque(self._request_times, cutoff)
            return len(self._request_times) < self._max_rpm

    # ------------------------------------------------------------------
    # Snapshot for /health
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a point-in-time metrics dict for the health endpoint."""
        now = time.monotonic()
        cutoff_1m = now - 60
        cutoff_5m = now - 300

        with self._lock:
            # Prune stale entries
            self._prune_deque(self._request_times, cutoff_5m)
            self._prune_deque(self._rejected_times, cutoff_5m)

            requests_1m = sum(1 for t in self._request_times if t >= cutoff_1m)
            requests_5m = len(self._request_times)

            rejected_1m = sum(1 for t in self._rejected_times if t >= cutoff_1m)

            # Simulation stats
            sim_stats: dict
            if self._sim_durations:
                durations = list(self._sim_durations)
                sim_stats = {
                    "min_ms": round(min(durations)),
                    "avg_ms": round(sum(durations) / len(durations)),
                    "max_ms": round(max(durations)),
                    "count": len(durations),
                }
            else:
                sim_stats = {"min_ms": 0, "avg_ms": 0, "max_ms": 0, "count": 0}

            # Build tool_stats snapshot
            tool_stats_snap: dict[str, dict] = {}
            for name, ts in self._tool_stats.items():
                total_calls = ts.successes + ts.errors
                avg_latency = (
                    round(ts.latency_sum_ms / total_calls, 1)
                    if total_calls > 0
                    else 0.0
                )
                tool_stats_snap[name] = {
                    "calls": ts.calls,
                    "successes": ts.successes,
                    "errors": ts.errors,
                    "avg_latency_ms": avg_latency,
                    "last_called": ts.last_called,
                }

            # Build hourly/daily history (oldest → newest)
            hourly_history = self._build_bucket_history(
                self._hourly_buckets, 24, 3600
            )
            daily_history = self._build_bucket_history(
                self._daily_buckets, 7, 86400
            )

            # Cumulative uptime
            session_uptime = now - self._start_time
            cumulative = self._cumulative_uptime_s + session_uptime

            # Circuit count
            circuit_count = 0
            if self._circuit_count_fn is not None:
                try:
                    circuit_count = self._circuit_count_fn()
                except Exception:
                    pass

            result = {
                # Original keys (backward compat)
                "uptime_seconds": round(now - self._start_time),
                "requests_last_1m": requests_1m,
                "requests_last_5m": requests_5m,
                "active_simulations": self._active_sims,
                "total_requests_by_tool": dict(self._tool_counts),
                "simulation_stats": sim_stats,
                "throttle": {
                    "rejected_last_1m": rejected_1m,
                    "rejected_total": self._rejected_total,
                    "max_rpm": self._max_rpm,
                },
                # New keys
                "server_start_time": datetime.datetime.fromtimestamp(
                    self._start_wall, tz=datetime.timezone.utc
                ).isoformat(),
                "cumulative_uptime_seconds": round(cumulative),
                "last_request_timestamp": self._last_request_ts,
                "circuit_count": circuit_count,
                "tool_stats": tool_stats_snap,
                "hourly_history": hourly_history,
                "daily_history": daily_history,
                "recent_errors": list(self._error_log),
                "high_water_marks": {
                    "peak_concurrent_sims": self._peak_concurrent_sims,
                    "peak_rpm": self._peak_rpm,
                    "peak_requests_per_hour": self._peak_requests_per_hour,
                },
                "system": self._collect_system_metrics(),
            }

        return result

    def _build_bucket_history(
        self, buckets: list[TimeBucket], size: int, period_seconds: int
    ) -> list[dict]:
        """Build ordered history array from ring-buffer buckets.

        Must be called with lock held.
        """
        current_epoch = int(time.time() // period_seconds)
        result: list[dict] = []
        for offset in range(size - 1, -1, -1):
            target_epoch = current_epoch - offset
            idx = target_epoch % size
            bucket = buckets[idx]
            if bucket.epoch_period == target_epoch and bucket.total > 0:
                avg_lat = round(bucket.latency_sum_ms / bucket.total, 1)
                result.append({
                    "total": bucket.total,
                    "errors": bucket.errors,
                    "avg_latency_ms": avg_lat,
                })
            else:
                result.append({"total": 0, "errors": 0, "avg_latency_ms": 0.0})
        return result

    # ------------------------------------------------------------------
    # System metrics (psutil)
    # ------------------------------------------------------------------

    def _collect_system_metrics(self) -> dict:
        """Collect system metrics via psutil. Caches for 60s.

        Returns dict with CPU/RAM/disk/process info, or error dict if
        psutil is not available.
        """
        now = time.monotonic()
        if self._system_metrics is not None and (now - self._system_metrics_ts) < 60:
            return self._system_metrics

        try:
            import psutil
        except ImportError:
            self._system_metrics = {"error": "psutil not installed"}
            self._system_metrics_ts = now
            return self._system_metrics

        try:
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            proc = psutil.Process()
            proc_mem = proc.memory_info()

            self._system_metrics = {
                "cpu_percent": psutil.cpu_percent(interval=None),
                "ram_percent": vm.percent,
                "ram_used_mb": round(vm.used / (1024 * 1024)),
                "disk_percent": disk.percent,
                "disk_used_gb": round(disk.used / (1024**3), 1),
                "process_cpu_percent": proc.cpu_percent(interval=None),
                "process_ram_mb": round(proc_mem.rss / (1024 * 1024)),
            }
        except Exception as exc:
            self._system_metrics = {"error": str(exc)[:200]}

        self._system_metrics_ts = now
        return self._system_metrics

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load persisted metrics from JSON file."""
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, OSError):
            logger.debug("Corrupt or unreadable metrics file; starting fresh",
                         exc_info=True)
            return

        with self._lock:
            self._cumulative_uptime_s = data.get("cumulative_uptime_seconds", 0.0)

            # Restore tool_stats
            for name, ts_data in data.get("tool_stats", {}).items():
                ts = ToolStats(
                    calls=ts_data.get("calls", 0),
                    successes=ts_data.get("successes", 0),
                    errors=ts_data.get("errors", 0),
                    latency_sum_ms=ts_data.get("latency_sum_ms", 0.0),
                    last_called=ts_data.get("last_called"),
                )
                self._tool_stats[name] = ts
                # Also restore backward-compat tool_counts
                self._tool_counts[name] = ts.calls

            # Restore high water marks
            hwm = data.get("high_water_marks", {})
            self._peak_concurrent_sims = hwm.get("peak_concurrent_sims", 0)
            self._peak_rpm = hwm.get("peak_rpm", 0)
            self._peak_requests_per_hour = hwm.get("peak_requests_per_hour", 0)

            # Restore time buckets
            for i, b_data in enumerate(data.get("hourly_buckets", [])):
                if i < 24:
                    self._hourly_buckets[i] = TimeBucket(
                        epoch_period=b_data.get("epoch_period", 0),
                        total=b_data.get("total", 0),
                        errors=b_data.get("errors", 0),
                        latency_sum_ms=b_data.get("latency_sum_ms", 0.0),
                    )
            for i, b_data in enumerate(data.get("daily_buckets", [])):
                if i < 7:
                    self._daily_buckets[i] = TimeBucket(
                        epoch_period=b_data.get("epoch_period", 0),
                        total=b_data.get("total", 0),
                        errors=b_data.get("errors", 0),
                        latency_sum_ms=b_data.get("latency_sum_ms", 0.0),
                    )

            # Restore error log
            for entry in data.get("error_log", []):
                self._error_log.append(entry)

            self._last_request_ts = data.get("last_request_timestamp")

    def save(self) -> None:
        """Serialize metrics and write atomically to disk."""
        with self._lock:
            data = self._serialize()

        # Ensure parent directory exists
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: tmp + os.replace
        tmp_path = self._persist_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
            os.replace(tmp_path, self._persist_path)
            try:
                os.chmod(self._persist_path, 0o600)
            except OSError:
                pass
        except OSError:
            logger.debug("Failed to write metrics file", exc_info=True)
            # Clean up tmp if replace failed
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _serialize(self) -> dict:
        """Build the dict to persist. Must be called with lock held."""
        session_uptime = time.monotonic() - self._start_time
        cumulative = self._cumulative_uptime_s + session_uptime

        tool_stats_data: dict[str, dict] = {}
        for name, ts in self._tool_stats.items():
            tool_stats_data[name] = asdict(ts)

        return {
            "cumulative_uptime_seconds": cumulative,
            "tool_stats": tool_stats_data,
            "high_water_marks": {
                "peak_concurrent_sims": self._peak_concurrent_sims,
                "peak_rpm": self._peak_rpm,
                "peak_requests_per_hour": self._peak_requests_per_hour,
            },
            "hourly_buckets": [asdict(b) for b in self._hourly_buckets],
            "daily_buckets": [asdict(b) for b in self._daily_buckets],
            "error_log": list(self._error_log),
            "last_request_timestamp": self._last_request_ts,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_circuit_counter(self, fn: Callable[[], int]) -> None:
        """Store callable for live circuit count."""
        self._circuit_count_fn = fn

    def start_persistence(self) -> None:
        """Start the background persistence thread."""
        if self._persist_thread is not None:
            return
        self._persist_thread = _PersistenceThread(self)
        self._persist_thread.start()

    def shutdown(self) -> None:
        """Stop persistence thread and do final save."""
        if self._persist_thread is not None:
            self._persist_thread.stop()
            self._persist_thread.join(timeout=5.0)
            self._persist_thread = None
        try:
            self.save()
        except Exception:
            logger.debug("Final metrics save failed", exc_info=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _prune_deque(dq: deque, cutoff: float) -> None:
        """Remove entries older than *cutoff* from the left of a deque."""
        while dq and dq[0] < cutoff:
            dq.popleft()
