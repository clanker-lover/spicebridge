# simulator.py

**Source:** `src/spicebridge/simulator.py`

## Purpose

Runs ngspice simulations with concurrency control, queue management, and dual-backend execution (spicelib first, subprocess fallback).

## Public API

- **`run_simulation(netlist, output_dir=None)`**: Main entry point. Writes netlist to disk, runs ngspice, returns `True` if a non-empty `.raw` file is produced. Creates temp dir if `output_dir` is None.
- **`validate_netlist_syntax(netlist)`**: Runs ngspice in batch mode to check for syntax errors. Returns `(is_valid, error_messages)`.
- **`SimulationQueueFull`**: Exception raised when queue depth exceeds `_MAX_SIM_QUEUE`.
- **`get_sim_queue_depth()`**: Returns number of requests waiting for a simulation slot.
- **`get_active_sims()`**: Returns number of currently running simulations.

## Concurrency Model

- `_sim_semaphore`: Threading semaphore limiting concurrent sims to `SPICEBRIDGE_MAX_CONCURRENT_SIMS` (default 3).
- `_queue_depth` / `_queue_lock`: Track waiting threads. If queue exceeds `SPICEBRIDGE_MAX_SIM_QUEUE` (default 5), raises `SimulationQueueFull`.
- `_SIMULATION_TIMEOUT`: 60 seconds per simulation.

## Execution Backends

1. **spicelib**: `NGspiceSimulator.run()` via `concurrent.futures.ThreadPoolExecutor` with timeout.
2. **subprocess fallback**: Direct `ngspice -b -r <raw> <netlist>` invocation.

## Dependencies

`spicelib.simulators.ngspice_simulator` (optional), `subprocess`, `threading`, `tempfile`.

## Architecture Role

Lowest-level execution layer. Called by [server.py](server.md) (wrapped with metrics) and [monte_carlo.py](monte_carlo.md). See [simulation-pipeline](../concepts/simulation-pipeline.md).
