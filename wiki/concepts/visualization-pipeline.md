# Visualization Pipeline

Cross-cutting concept appearing in: `schematic.py`, `svg_renderer.py`, `kicad_export.py`, `web_viewer.py`, `schematic_cache.py`, `server.py`

## FACTS

SPICEBridge has four visualization outputs. All of them draw the same scene: `kicad_export.build_scene()` parses the netlist (`parse_netlist()` from `schematic.py`), places the components and routes the wires.

### 1. Static Schematic
- **Module**: `schematic.py` -> `draw_schematic()`
- **Output**: PNG or SVG file, drawn by `render_svg(theme="light")`; PNG via cairosvg
- **Used by**: `draw_schematic` tool in `server.py`
- **For**: Inline image delivery to MCP clients

### 2. Interactive SVG
- **Module**: `svg_renderer.py` -> `render_svg()`
- **Output**: SVG string with data attributes and CSS hover effects, using the graphics of the embedded KiCad lib symbols
- **Used by**: Web viewer API (`/api/circuit/{id}/svg`)
- **For**: Browser-based interactive viewing with simulation overlay

### 3. KiCad Export
- **Module**: `kicad_export.py` -> `export_kicad_schematic()`
- **Output**: `.kicad_sch` file in KiCad 8 S-expression format
- **Used by**: `export_kicad` tool in `server.py`
- **For**: PCB design workflow integration

### 4. Cloud Schematic Serving
- **Module**: `schematic_cache.py` + `server.py` HTTP routes
- **Flow**: SVG -> cairosvg -> PNG -> cache -> HTTP serve at `/schematics/{id}.png`
- **For**: Public URL accessible from Claude.ai (users cannot see inline MCP images)

## Shared Layout

The static schematic, the interactive SVG and the KiCad export use one layout and one router (`kicad_export.build_scene()`), so all three show the same drawing:
- Nets get columns following the signal flow from the signal sources; supply rails do not take part.
- 2-pin parts between two nets sit on the main row between their columns; feedback parts in rows above it.
- Parts to ground and multi-pin parts hang below their net, parts to a supply stand above with the supply as a rail on top.
- A maze router on a 1.27 mm grid connects the pins, crossing other nets only at right angles; nets it cannot route get net labels.

## INFERENCES

The schematic URL emphasis in server.py (multiple `_assistant_hint` fields, duplicate TextContent blocks) reflects a real UX problem: Claude.ai cannot render MCP inline images, so the public URL is the only way users see schematics.

## Related Pages

- [schematic.py](../summaries/schematic.md), [svg_renderer.py](../summaries/svg_renderer.md), [kicad_export.py](../summaries/kicad_export.md)
- [web_viewer.py](../summaries/web_viewer.md), [schematic_cache.py](../summaries/schematic_cache.md)
