# parser.py

**Source:** `src/spicebridge/parser.py`

## Purpose

Extracts structured metrics from ngspice `.raw` output files. Supports AC, transient, and DC operating point analysis types.

## Public API

- **`parse_results(raw_path)`**: Auto-detects analysis type and dispatches to the appropriate parser. Returns a dict with analysis-specific metrics.
- **`parse_ac(raw_path)`**: Returns `f_3dB_hz`, `gain_dc_dB`, `rolloff_rate_dB_per_decade`, `phase_at_f3dB_deg`, `peak_gain_dB`, `peak_gain_freq_hz`, `num_points`, `freq_range`.
- **`parse_transient(raw_path)`**: Returns `steady_state_value`, `peak_value`, `rise_time_10_90_s`, `overshoot_pct`, `settling_time_1pct_s`.
- **`parse_dc_op(raw_path)`**: Returns dict of `nodes` mapping node names to voltage values.
- **`read_ac_at_frequency(raw_path, frequency_hz)`**: Interpolates gain/phase at a specific frequency.
- **`read_ac_bandwidth(raw_path, threshold_db)`**: Finds cutoff frequency at an arbitrary threshold.
- **`detect_analysis_type(raw_path)`**: Returns plot name string from `.raw` file.

## Key Implementation Details

- Uses `spicelib.RawRead` with `dialect="ngspice"` to parse `.raw` files.
- `_select_output_trace()`: Heuristic trace selection -- prefers `v(out)`, then first `v(...)` not in `{v(in), v(v1)}`.
- `_sanitize_array()`: Replaces NaN values with 0.0, emits warnings.
- f_3dB calculation uses linear interpolation between adjacent points crossing the -3dB threshold.

## Dependencies

`numpy`, `spicelib.RawRead`.

## Architecture Role

Post-simulation analysis layer. Called by [server.py](server.md) and [monte_carlo.py](monte_carlo.md). See [simulation-pipeline](../concepts/simulation-pipeline.md).
