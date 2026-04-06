# constants.py

**Source:** `src/spicebridge/constants.py`

## Purpose

Shared constants used across multiple SPICEBridge modules. Centralizes SPICE component node counts and analysis command regex patterns.

## Public API

- **`COMPONENT_NODE_COUNTS`**: Dict mapping component type letters to their number of nodes. R/C/L/V/I/D/B/F/H -> 2, Q/J -> 3, M/E/G -> 4.
- **`ANALYSIS_RE`**: Compiled regex matching `.ac`, `.tran`, `.op`, `.dc` lines (case-insensitive).
- **`END_RE`**: Compiled regex matching `.end` lines.

## Usage

- `COMPONENT_NODE_COUNTS` is used by [schematic.py](schematic.md), [composer.py](composer.md), and their dependents for correct netlist parsing.
- `ANALYSIS_RE` and `END_RE` are used by [netlist_utils.py](netlist_utils.md) and [composer.py](composer.md) to strip analysis commands during netlist preparation and composition.

## Dependencies

`re`. No spicebridge imports.
