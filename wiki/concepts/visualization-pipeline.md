# Visualization Pipeline

Cross-cutting concept appearing in: `schematic.py`, `svg_renderer.py`, `kicad_export.py`, `web_viewer.py`, `schematic_cache.py`, `server.py`

## FACTS

SPICEBridge has four visualization outputs, all sharing the `parse_netlist()` function from `schematic.py` as their common entry point:

### 1. Static Schematic (schemdraw)
- **Module**: `schematic.py` -> `draw_schematic()`
- **Output**: PNG or SVG file via schemdraw library
- **Used by**: `draw_schematic` tool in `server.py`
- **For**: Inline image delivery to MCP clients

### 2. Interactive SVG
- **Module**: `svg_renderer.py` -> `render_svg()`
- **Output**: SVG string with data attributes and CSS hover effects
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

## Shared Layout Pattern

All three renderers (schemdraw, SVG, KiCad) use the same column-based layout algorithm:
- Column 1: Sources (V, I) -- vertical
- Column 2: Series components -- horizontal/rotated
- Column 3: Shunt components (ground-connected) -- vertical with ground symbol

This consistency means schematics look similar regardless of output format.

## INFERENCES

The schematic URL emphasis in server.py (multiple `_assistant_hint` fields, duplicate TextContent blocks) reflects a real UX problem: Claude.ai cannot render MCP inline images, so the public URL is the only way users see schematics.

## Related Pages

- [schematic.py](../summaries/schematic.md), [svg_renderer.py](../summaries/svg_renderer.md), [kicad_export.py](../summaries/kicad_export.md)
- [web_viewer.py](../summaries/web_viewer.md), [schematic_cache.py](../summaries/schematic_cache.md)
