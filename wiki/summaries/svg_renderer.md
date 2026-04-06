# svg_renderer.py

**Source:** `src/spicebridge/svg_renderer.py`

## Purpose

Interactive SVG schematic renderer with data attributes for the web viewer. Produces dark-themed SVGs with hover effects, net labels, and optional simulation result overlays.

## Public API

- **`render_svg(netlist, results=None, width=800, height=600)`**: Renders a SPICE netlist as an interactive SVG string. Optionally overlays DC operating point voltages on nodes.

## Layout Engine

Uses column-based placement:
- **Column 1** (`x=100`): Sources (V, I) -- vertical orientation.
- **Column 2** (`x=300`): Series components -- rotated 90 degrees (horizontal).
- **Column 3** (`x=500`): Shunt components -- vertical with ground symbols.

Spacing: `_Y_SPACING = 120` pixels between components.

## Component Symbols

Hand-drawn SVG paths for each component type: resistor (zigzag), capacitor (parallel plates), inductor (semicircular bumps), voltage source (circle with +/-), current source (circle with arrow), diode (triangle + bar), BJT (vertical bar + diagonal leads), MOSFET (gate oxide bar + channel), and generic rectangle for subcircuits.

## Wire Routing

Manhattan L-routing: connects pins sharing the same net name. Horizontal first, then vertical. Junction dots placed at multi-connection points.

## Interactive Features

SVG elements carry `data-ref`, `data-type`, `data-value`, `data-node` attributes. CSS hover effects highlight components and labels in yellow (`#ffeb3b`). Dark theme with cyan components (`#4fc3f7`), green labels (`#81c784`), purple net labels (`#ce93d8`).

## Dependencies

`xml.etree.ElementTree`, `math`, `spicebridge.schematic` (ParsedComponent, parse_netlist, _is_ground).

## Architecture Role

Interactive visualization layer. Called by [web_viewer.py](web_viewer.md) and [server.py](server.md). See [visualization-pipeline](../concepts/visualization-pipeline.md).
