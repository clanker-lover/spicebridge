# Circuit Lifecycle

Cross-cutting concept appearing in: `circuit_manager.py`, `server.py`, `composer.py`

## FACTS

A circuit passes through the following states (Source: `circuit_manager.py`, `server.py`):

### Creation
1. `create_circuit(netlist)` or `load_template(template_id)` is called.
2. Netlist is sanitized via `sanitize_netlist()`.
3. `CircuitManager.create()` generates a UUID hex ID, creates a temp directory, stores the `CircuitState`.
4. Ports are auto-detected from node names (heuristic: `in`->input, `out`->output, etc.) via `auto_detect_ports()`.
5. If circuit count reaches 100 (`_MAX_CIRCUITS`), the oldest circuit is evicted and its temp dir deleted.

### Simulation
6. A simulation tool (e.g., `run_ac_analysis`) retrieves the circuit, prepares the netlist, runs ngspice.
7. Results are stored via `update_results(circuit_id, results)`.
8. Web viewer is notified of results update.

### Modification
9. `modify_component()` updates the netlist in-place via `update_netlist()`.
10. Web viewer is notified of circuit update.

### Composition
11. `connect_stages()` retrieves multiple circuits, composes them via `compose_stages()`, and creates a new circuit with the combined netlist.

### Deletion
12. `delete_circuit()` removes the circuit state and cleans up the temp directory.
13. `cleanup_all()` runs at process exit via `atexit`.

## INFERENCES

Circuits are ephemeral session state -- they exist only in memory and temp directories. There is no persistence across server restarts. The 100-circuit limit with FIFO eviction prevents memory exhaustion from accumulated sessions.

## OPEN QUESTIONS

- Should there be a TTL-based eviction in addition to count-based? Long-running servers could accumulate stale circuits that never reach the 100 limit.

## Related Pages

- [circuit_manager.py](../summaries/circuit_manager.md), [server.py](../summaries/server.md), [composer.py](../summaries/composer.md)
