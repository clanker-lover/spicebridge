# composer.py

**Source:** `src/spicebridge/composer.py`

## Purpose

Multi-stage circuit composition engine. Enables connecting multiple circuit stages (e.g., filter + amplifier) into a single combined netlist with automatic port wiring and node prefixing.

## Public API

- **`auto_detect_ports(netlist)`**: Scans a netlist and returns a port mapping based on node-name heuristics (e.g., `in` -> `input`, `out` -> `output`, `0` -> `ground`).
- **`compose_stages(stages, connections=None, shared_ports=None)`**: Composes multiple stages into one netlist. Returns `{"netlist": str, "ports": dict, "stages": list}`.
- **`prefix_netlist(netlist, prefix, preserve_nodes=None, strip_sources_on=None)`**: Prefixes all component refs and nodes in a netlist. Returns `(prefixed_netlist, subckt_blocks)`.

## Composition Algorithm

1. Assigns default labels (S1, S2, ...) to stages.
2. Auto-builds connections if none provided (out -> in between consecutive stages).
3. Validates all connection indices and port names.
4. Prefixes each stage's netlist (component refs, nodes, `.param` keys) to avoid name collisions.
5. Renames nodes at connection points to shared wire names.
6. Deduplicates `.subckt` blocks and `.include` lines.
7. Assembles final netlist with stage comments.

## Key Design Decisions

- **Pure text processing**: No imports from other spicebridge domain modules (only `constants`). This keeps composition logic isolated.
- `.subckt` blocks are extracted verbatim (not prefixed).
- Analysis directives (`.ac`, `.tran`, `.op`, `.dc`, `.end`) are stripped during prefixing.
- Node `"0"` (ground) is always preserved across all stages.

## Dependencies

`spicebridge.constants` (regex patterns, node counts). No other spicebridge imports.

## Architecture Role

Enables multi-stage design workflow. Called by [server.py](server.md) `connect_stages` tool. See [multi-stage-composition](../concepts/multi-stage-composition.md).
