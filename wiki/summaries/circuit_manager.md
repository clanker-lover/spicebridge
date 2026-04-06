# circuit_manager.py

**Source:** `src/spicebridge/circuit_manager.py`

## Purpose

Thread-safe in-memory state management for circuit sessions. Each circuit gets a unique ID, a stored netlist, an output directory for simulation artifacts, and optional port definitions.

## Public API

- **`CircuitManager`**: Main class.
  - `create(netlist)`: Creates a new circuit, returns UUID hex ID. Evicts oldest circuit if at `_MAX_CIRCUITS` (100).
  - `get(circuit_id)`: Returns `CircuitState`. Raises `KeyError` if not found.
  - `update_results(circuit_id, results)`: Stores last simulation results.
  - `update_netlist(circuit_id, netlist)`: Replaces stored netlist.
  - `set_ports(circuit_id, ports)` / `get_ports(circuit_id)`: Port definition management.
  - `delete(circuit_id)`: Removes circuit and cleans up output directory.
  - `cleanup_all()`: Registered as `atexit` handler. Removes all temp directories.
  - `list_all()`: Returns summary info for all circuits.
  - `circuit_count()`: Returns count of stored circuits.

- **`CircuitState`** dataclass: `circuit_id`, `netlist`, `output_dir` (Path), `last_results` (dict|None), `ports` (dict|None), `created_at` (monotonic time).

## Concurrency

All methods use `threading.Lock` for thread safety. Internal `_get_unlocked()` is used for operations already holding the lock.

## Lifecycle

- Output directories are created via `tempfile.mkdtemp()` with prefix `spicebridge_{circuit_id}_`.
- Directories are cleaned up on `delete()`, eviction, or process exit (`atexit`).

## Dependencies

`uuid`, `tempfile`, `shutil`, `threading`, `atexit`.

## Architecture Role

Session state layer. Used by [server.py](server.md) as `_manager` singleton. See [circuit-lifecycle](../concepts/circuit-lifecycle.md).
