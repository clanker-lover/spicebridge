"""In-memory FIFO-evicting cache for schematic PNG bytes."""

from __future__ import annotations

import threading


class SchematicCache:
    """Thread-safe cache for PNG bytes, keyed by circuit_id.

    Uses dict insertion order for FIFO eviction when *max_size* is reached.
    Tracks hit/miss counts for monitoring.
    """

    def __init__(self, max_size: int = 50) -> None:
        self._data: dict[str, bytes] = {}
        self._max_size = max_size
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def put(self, circuit_id: str, png_bytes: bytes) -> None:
        """Store *png_bytes* under *circuit_id*, evicting oldest if full."""
        with self._lock:
            # Remove first so re-insert refreshes insertion order
            self._data.pop(circuit_id, None)
            if len(self._data) >= self._max_size:
                oldest = next(iter(self._data))
                del self._data[oldest]
            self._data[circuit_id] = png_bytes

    def get(self, circuit_id: str) -> bytes | None:
        """Return cached PNG bytes, or ``None`` if not present."""
        with self._lock:
            result = self._data.get(circuit_id)
            if result is not None:
                self._hits += 1
            else:
                self._misses += 1
            return result

    def delete(self, circuit_id: str) -> None:
        """Remove entry for *circuit_id* (no-op if absent)."""
        with self._lock:
            self._data.pop(circuit_id, None)

    def stats(self) -> dict:
        """Return cache statistics for monitoring."""
        with self._lock:
            return {
                "size": len(self._data),
                "max": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
