# Simulation Pipeline

Cross-cutting concept appearing in: `server.py`, `simulator.py`, `parser.py`, `netlist_utils.py`, `monte_carlo.py`

## FACTS

The simulation pipeline follows a fixed sequence across all analysis types (Source: `server.py`):

1. **Retrieve circuit** -- `_manager.get(circuit_id)` fetches the stored netlist and output directory.
2. **Prepare netlist** -- `prepare_netlist(netlist, analysis_line)` strips existing analysis commands and appends the new one with `.end` (Source: `netlist_utils.py`).
3. **Run simulation** -- `run_simulation(prepared, output_dir)` writes the netlist to disk and invokes ngspice. The server wraps this with metrics timing (Source: `server.py` lines 100-108).
4. **Parse results** -- `parse_results(raw_path)` auto-detects the analysis type from the `.raw` file and extracts structured metrics (Source: `parser.py`).
5. **Store results** -- `_manager.update_results(circuit_id, results)` saves the parsed dict for later retrieval.
6. **Notify viewer** -- If the web viewer is active, `viewer.notify_change()` broadcasts the update via WebSocket.

## Simulation Backends

ngspice execution has two backends tried in order (Source: `simulator.py`):
1. **spicelib**: `NGspiceSimulator.run()` in a ThreadPoolExecutor with 60s timeout.
2. **subprocess**: Direct `ngspice -b -r <raw> <netlist>` invocation with 60s timeout.

Both produce a `.raw` file that is read by spicelib's `RawRead` parser.

## Concurrency Control

- Semaphore limits concurrent simulations to 3 (configurable via `SPICEBRIDGE_MAX_CONCURRENT_SIMS`).
- Queue depth tracking rejects requests when 5+ are waiting (configurable via `SPICEBRIDGE_MAX_SIM_QUEUE`).
- SimulationQueueFull exception propagates cleanly through the `_monitored` decorator.

## INFERENCES

The dual-backend approach provides resilience -- if spicelib has compatibility issues with a particular ngspice version, the subprocess fallback still works. The queue-before-semaphore pattern prevents unbounded thread accumulation under load.

## Related Pages

- [simulator.py](../summaries/simulator.md), [parser.py](../summaries/parser.md), [netlist_utils.py](../summaries/netlist_utils.md)
- [tolerance-analysis](tolerance-analysis.md) -- Monte Carlo uses this pipeline
