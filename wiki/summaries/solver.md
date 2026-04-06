# solver.py

**Source:** `src/spicebridge/solver.py`

## Purpose

Design equation solvers for 12 common circuit topologies. Given target specs (e.g., cutoff frequency, gain), calculates component values and snaps them to standard E-series values.

## Public API

- **`solve(topology_id, specs)`**: Main entry point. Dispatches to topology-specific solver. Returns dict with `components`, `equations_used`, `notes`, `nearest_standard`.

## Supported Topologies

| Topology | Key Specs |
|---|---|
| `rc_lowpass_1st` | `f_cutoff_hz` |
| `rc_highpass_1st` | `f_cutoff_hz` |
| `sallen_key_lowpass_2nd` | `f_cutoff_hz`, `Q` (default 0.707) |
| `sallen_key_hpf_2nd` | `f_cutoff_hz`, `Q` |
| `inverting_opamp` | `gain_dB` or `gain_linear`, `input_impedance_ohms` |
| `noninverting_opamp` | `gain_dB` or `gain_linear` |
| `voltage_divider` | `ratio` or (`output_voltage` + `input_voltage`) |
| `mfb_bandpass` | `f_center_hz`, `Q`, `gain_linear` |
| `summing_amplifier` | `num_inputs`, `gain_per_input`, `input_impedance_ohms` |
| `differential_amp` | `gain_linear`, `input_impedance_ohms` |
| `instrumentation_amp` | `gain_linear`, `r_bridge` |
| `twin_t_notch` | `f_notch_hz` |

## Design Methodology

- `_pick_c_anchor(f_c)`: Selects a capacitor value that puts the companion resistor in a practical range (100 ohm to 1M ohm). Tries candidates: 10n, 1n, 100n, 100p, 1u.
- `_build_nearest()`: Snaps all computed values to E96 standard series.
- `_check_resistor()` / `_check_capacitor()`: Adds warning notes if values fall outside practical ranges.

## Dependencies

`math`, `spicebridge.standard_values` (format_engineering, snap_to_standard).

## Architecture Role

Calculation layer. Called by [server.py](server.md) `calculate_components` and `load_template` (with specs). See [template-system](../concepts/template-system.md).
