# schematic.py

**Source:** `src/spicebridge/schematic.py`

## Purpose

Parses SPICE netlist text into component objects and saves static schematic images (PNG or SVG). The drawing itself comes from [svg_renderer.py](svg_renderer.md), so it matches the web viewer and the KiCad export.

## Public API

- **`parse_netlist(netlist)`**: Parses a SPICE netlist into a list of `ParsedComponent` objects. The first line is the title and is ignored. Skips comments (`*`), directives (`.`), continuations (`+`), blank lines and `.subckt ... .ends` bodies. Handles subcircuit instances (`X`, parameters are not nodes) and the behavioral forms of `E`/`G` sources (`VALUE=`, `POLY(n)`, `TABLE`, ...), which keep only their two output nodes.
- **`draw_schematic(netlist, output_path, fmt="png")`**: Renders the netlist with `render_svg(..., theme="light")` (KiCad colours) and saves it; PNG is converted from the SVG with cairosvg. Returns the output `Path`. Raises `ValueError` if the netlist has no components.

## Key Types

- **`ParsedComponent`** dataclass: `comp_type` (single letter), `ref` (e.g., "R1"), `nodes` (list), `value` (string).

## Dependencies

`cairosvg` (PNG output), `spicebridge.constants.COMPONENT_NODE_COUNTS`, [svg_renderer.py](svg_renderer.md) (imported lazily, since it depends on this module).

## Architecture Role

Parsing and static visualization. Called by [server.py](server.md) `draw_schematic` tool and the auto-design loop. `parse_netlist` is also used by [svg_renderer.py](svg_renderer.md), [kicad_export.py](kicad_export.md) and [web_viewer.py](web_viewer.md). See [visualization-pipeline](../concepts/visualization-pipeline.md).
