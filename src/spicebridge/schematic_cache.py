"""In-memory FIFO-evicting cache for schematic PNG bytes."""

from __future__ import annotations

import threading


class SchematicCache:
    """Thread-safe cache for PNG bytes, keyed by circuit_id.

    Uses dict insertion order for FIFO eviction when *max_size* is reached.
    """

    def __init__(self, max_size: int = 50) -> None:
        self._data: dict[str, bytes] = {}
        self._max_size = max_size
        self._lock = threading.Lock()

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
            return self._data.get(circuit_id)

    def delete(self, circuit_id: str) -> None:
        """Remove entry for *circuit_id* (no-op if absent)."""
        with self._lock:
            self._data.pop(circuit_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
