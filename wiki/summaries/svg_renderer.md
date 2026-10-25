# svg_renderer.py

**Source:** `src/spicebridge/svg_renderer.py`

## Purpose

SVG schematic renderer for the web viewer and the `draw_schematic` tool. It draws the same placed and routed schematic as the KiCad export, so the picture on screen and the exported `.kicad_sch` match.

## Public API

- **`render_svg(netlist, results=None, width=800, height=600, theme="dark")`**: Renders a SPICE netlist as an SVG string. `results` overlays DC operating point voltages on the nodes. `width`/`height` of `None` use the drawing's natural size. `theme` is `"dark"` (web viewer) or `"light"` (KiCad colours, used by `draw_schematic`).

## How It Works

1. `kicad_export.build_scene()` parses, places (signal-flow layout) and routes (maze router) the netlist.
2. Each component is drawn from the graphics of its embedded KiCad lib symbol (polylines, rectangles, circles, arcs, pins and pin names), placed and rotated exactly like in KiCad.
3. Wires, junctions, GND symbols, net labels and the Reference/Value fields come from the same scene.

Coordinates are KiCad millimetres scaled to 8 SVG units per mm.

## Interactive Features

Components are `<g class="component">` groups with `id="component-<ref>"`, `data-ref`, `data-type` and `data-value`. Wires carry `data-node` with their net name, as do net labels and simulation annotations, so the viewer can highlight a whole net. CSS hover effects highlight components and nets.

## Dependencies

`xml.etree.ElementTree`, [kicad_export.py](kicad_export.md) (`build_scene`, field and pin geometry), [schematic.py](schematic.md) (`parse_netlist`).

## Architecture Role

Visualization layer. Called by [web_viewer.py](web_viewer.md) and, through `schematic.draw_schematic`, by [server.py](server.md). See [visualization-pipeline](../concepts/visualization-pipeline.md).
