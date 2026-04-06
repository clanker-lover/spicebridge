# netlist_utils.py

**Source:** `src/spicebridge/netlist_utils.py`

## Purpose

Shared netlist utility functions. Currently contains a single function for preparing netlists for simulation.

## Public API

- **`prepare_netlist(netlist, analysis_line)`**: Strips existing analysis commands (`.ac`, `.tran`, `.op`, `.dc`) and `.end` directives from a netlist, then appends the given analysis line and `.end`. Returns the prepared netlist string.

## Rationale

FACTS: Templates and user-supplied netlists may contain their own analysis commands. The simulation tools need to inject their own analysis lines. This function ensures no conflicting commands exist. (Source: `src/spicebridge/netlist_utils.py`)

## Dependencies

`spicebridge.constants` (ANALYSIS_RE, END_RE).

## Architecture Role

Utility layer. Called by [server.py](server.md) for every simulation tool and by [monte_carlo.py](monte_carlo.md) `run_single_sim`. See [simulation-pipeline](../concepts/simulation-pipeline.md).
