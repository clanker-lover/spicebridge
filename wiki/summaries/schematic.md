# schematic.py

**Source:** `src/spicebridge/schematic.py`

## Purpose

Generates static schematic diagrams from SPICE netlists using the schemdraw library. Parses netlist text into component objects and renders them as PNG or SVG.

## Public API

- **`parse_netlist(netlist)`**: Parses a SPICE netlist into a list of `ParsedComponent` objects. Skips comments (`*`), directives (`.`), continuations (`+`), and blank lines. Handles subcircuit instances (`X`) specially.
- **`draw_schematic(netlist, output_path, fmt="png")`**: Renders a schematic and saves to file. Returns the output `Path`. Raises `ValueError` if netlist has no components.

## Key Types

- **`ParsedComponent`** dataclass: `comp_type` (single letter), `ref` (e.g., "R1"), `nodes` (list), `value` (string).

## Layout Algorithm

Components are classified into three groups and drawn in order:
1. **Sources** (V, I): Drawn going UP from ground on the left.
2. **Series** (non-ground connected): Drawn going RIGHT.
3. **Shunt** (at least one ground node): Drawn going DOWN with ground symbol.

Node positions are tracked to connect shunt components to their signal nodes.

## Component Mapping

Maps SPICE component letters to schemdraw elements: R->Resistor, C->Capacitor, L->Inductor, V->SourceV (or SourceSin for AC), I->SourceI, D->Diode, Q->BjtNpn, M->NFet, X->Opamp.

## Dependencies

`schemdraw`, `schemdraw.elements`, `spicebridge.constants.COMPONENT_NODE_COUNTS`.

## Architecture Role

Visualization layer (static). Called by [server.py](server.md) `draw_schematic` tool. The `parse_netlist` function is also reused by [svg_renderer.py](svg_renderer.md), [kicad_export.py](kicad_export.md), and [web_viewer.py](web_viewer.md). See [visualization-pipeline](../concepts/visualization-pipeline.md).
