# schematic_cache.py

**Source:** `src/spicebridge/schematic_cache.py`

## Purpose

In-memory FIFO-evicting cache for schematic PNG bytes. Enables serving cached schematics over HTTP without re-rendering.

## Public API

- **`SchematicCache`**: Thread-safe cache class.
  - `put(circuit_id, png_bytes)`: Stores PNG bytes, evicts oldest if at capacity.
  - `get(circuit_id)`: Returns cached bytes or None. Tracks hit/miss counts.
  - `delete(circuit_id)`: Removes entry (no-op if absent).
  - `stats()`: Returns `{size, max, hits, misses}` dict.
  - `__len__()`: Returns current cache size.

## Implementation

Uses dict insertion order for FIFO eviction. Default `max_size=50`. Re-insertion refreshes position (pop + insert).

## Dependencies

`threading`. No spicebridge imports.

## Architecture Role

Caching layer for the `/schematics/{circuit_id}.png` HTTP endpoint in [server.py](server.md). See [visualization-pipeline](../concepts/visualization-pipeline.md).
