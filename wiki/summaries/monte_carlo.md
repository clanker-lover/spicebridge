# monte_carlo.py

**Source:** `src/spicebridge/monte_carlo.py`

## Purpose

Monte Carlo and worst-case analysis infrastructure. Provides component tolerance analysis by randomizing or systematically varying R/C/L values and running multiple simulations.

## Public API

- **`parse_component_values(netlist)`**: Extracts R/C/L component values from a netlist. Returns list of `ComponentInfo`. Scans `.param` lines first (higher priority), then instance lines. Skips parameterized values containing `{`.
- **`randomize_values(components, tolerances, default_tol, rng)`**: Generates random values using Gaussian distribution (3-sigma = tolerance percentage).
- **`apply_corner(components, tolerances, default_tol, corner)`**: Applies deterministic corner variations (+1/-1/0 per component).
- **`substitute_values(netlist, components, values)`**: Replaces component values in the netlist string.
- **`generate_corners(n)`**: Returns all 2^N corners for N components.
- **`run_single_sim(netlist, analysis_cmd)`**: Runs one simulation with the given analysis command. Returns parsed results dict or None on failure.
- **`compute_statistics(results_list)`**: Computes mean, std, min, max, median, pct_5, pct_95 across simulation results.
- **`compute_worst_case(nominal, corner_results, components, tolerances, default_tol)`**: Finds min/max value + corner label for each numeric metric.
- **`compute_sensitivity(nominal, components, sensitivity_runs, tolerances, default_tol)`**: Computes per-component sensitivity as percent change per percent tolerance.

## Key Types

- **`ComponentInfo`** dataclass: `ref`, `value`, `value_str`, `line_num`, `source` ("param"/"instance").

## Tolerance Resolution

`_resolve_tolerance()` checks: exact ref match > component type prefix (R/C/L) > default tolerance. Case-insensitive.

## Dependencies

`numpy`, `spicebridge.netlist_utils`, `spicebridge.parser`, `spicebridge.simulator`, `spicebridge.standard_values`.

## Architecture Role

Advanced analysis layer. Called by [server.py](server.md) `run_monte_carlo` and `run_worst_case` tools. See [tolerance-analysis](../concepts/tolerance-analysis.md).
