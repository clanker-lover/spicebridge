# standard_values.py

**Source:** `src/spicebridge/standard_values.py`

## Purpose

E-series standard component value handling and engineering notation formatting. Provides the bridge between calculated ideal values and real-world purchasable components.

## Public API

- **`snap_to_standard(value, series="E96")`**: Snaps a value to the nearest standard E-series value using logarithmic distance. Supports E12 (12 values/decade), E24 (24), E96 (96).
- **`format_engineering(value)`**: Formats a float using SI prefixes. Examples: `10000.0` -> `"10k"`, `15.9e-9` -> `"15.9n"`, `4.7e-6` -> `"4.7u"`.
- **`parse_spice_value(s)`**: Converts SPICE value strings to floats. Supports suffixes: `f` (1e-15), `p` (1e-12), `n` (1e-9), `u` (1e-6), `m` (1e-3), `k` (1e3), `meg` (1e6), `t` (1e12), `g` (1e9).

## E-Series Data

- `E12`: 12 values per decade (1.0, 1.2, 1.5, ... 8.2)
- `E24`: 24 values per decade (1.0, 1.1, 1.2, ... 9.1)
- `E96`: 96 values per decade (1.00, 1.02, 1.05, ... 9.76)

## SPICE Value Note

SPICE uses `m` for milli (1e-3) and `meg` for mega (1e6). This differs from standard SI where `M` means mega. The `parse_spice_value` function handles `meg` correctly.

## Dependencies

`math`. No spicebridge imports.

## Architecture Role

Utility layer. Used by [solver.py](solver.md), [monte_carlo.py](monte_carlo.md), and [server.py](server.md). See [template-system](../concepts/template-system.md).
