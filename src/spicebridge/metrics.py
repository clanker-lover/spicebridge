"""Lightweight in-memory metrics collector for SPICEBridge.

No external dependencies — just counters, gauges, and rolling windows.
All methods are thread-safe.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class ServerMetrics:
    """Collects request counts, simulation timing, and throttle rejections."""

    def __init__(self, max_rpm: int = 60) -> None:
        self._lock = threading.Lock()
        self._start_time = time.monotonic()

        # Per-tool request counters (total since startup)
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

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_request(self, tool_name: str) -> None:
        """Record an incoming tool call."""
        now = time.monotonic()
        with self._lock:
            self._tool_counts[tool_name] += 1
            self._request_times.append(now)

    def record_sim_start(self) -> None:
        """Increment active simulation gauge."""
        with self._lock:
            self._active_sims += 1

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

            return {
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
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _prune_deque(dq: deque, cutoff: float) -> None:
        """Remove entries older than *cutoff* from the left of a deque."""
        while dq and dq[0] < cutoff:
            dq.popleft()
