# Tolerance Analysis

Cross-cutting concept appearing in: `monte_carlo.py`, `server.py`

## FACTS

SPICEBridge provides two tolerance analysis methods (Source: `server.py`, `monte_carlo.py`):

### Monte Carlo Analysis
- Randomizes R/C/L component values using Gaussian distribution (3-sigma = tolerance %).
- Runs N simulations (default varies, max 100 per `_MAX_MONTE_CARLO_RUNS`).
- Computes statistics: mean, std, min, max, median, 5th/95th percentiles.
- Timeout: 30 minutes for the full run.

### Worst-Case Analysis
- Deterministic: generates all 2^N corners for N components (each at +tolerance or -tolerance).
- Limited to 20 components (`_MAX_WORST_CASE_COMPONENTS`) and 500 simulations (`_MAX_WORST_CASE_SIMS`).
- Reports min/max value with corner label for each metric.
- Also computes per-component sensitivity (% change per % tolerance).

### Tolerance Resolution
Both methods support per-component tolerance overrides (Source: `monte_carlo.py`):
1. Exact ref match (e.g., `"R1": 1`)
2. Type prefix match (e.g., `"R": 5` for all resistors)
3. Default tolerance (e.g., 5%)

### Component Value Extraction
`parse_component_values()` extracts R/C/L values from the netlist. `.param` lines take priority over instance lines. Parameterized values (containing `{`) are skipped.

## INFERENCES

The 2^N corner explosion is why worst-case limits components to 20 (2^20 = 1M corners, but capped at 500 sims). Sensitivity analysis uses single-component perturbation instead of full corners, making it O(2N) rather than O(2^N).

## Related Pages

- [monte_carlo.py](../summaries/monte_carlo.md), [server.py](../summaries/server.md)
- [simulation-pipeline](simulation-pipeline.md) -- each MC run uses the full pipeline
