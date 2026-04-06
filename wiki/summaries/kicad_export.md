# kicad_export.py

**Source:** `src/spicebridge/kicad_export.py`

## Purpose

Exports SPICE netlists to KiCad 8 schematic (`.kicad_sch`) files. Maps SPICE components to KiCad library symbols, performs column-based layout, and generates wire routing.

## Public API

- **`export_kicad_schematic(netlist, output_dir, filename=None)`**: Parses the netlist, lays out components, routes wires, and writes a `.kicad_sch` file. Returns `(output_path, warnings)`.

## Symbol Mapping

Maps SPICE component types to KiCad library symbols:
- R -> `Device:R`, C -> `Device:C`, L -> `Device:L`, D -> `Device:D`
- V -> `Simulation_SPICE:VDC`, I -> `Simulation_SPICE:IDC`
- Q -> `Device:Q_NPN_BCE` or `Device:Q_PNP_BCE` (detected from value string)
- M -> `Device:Q_NMOS_GDS` or `Device:Q_PMOS_GDS`
- X -> `Simulation_SPICE:SUBCKT`

## Layout

Uses KiCad 2.54mm grid snapping. Three columns:
- Sources at x=50.8mm, Series at x=101.6mm, Shunt at x=152.4mm.
- Y spacing: 15.24mm between components.

## Wire Routing

Manhattan L-routing with grid snapping. Junctions placed at multi-connection points. Ground pins get `power:GND` symbols.

## Dependencies

`spicebridge.schematic` (parse_netlist, ParsedComponent), `spicebridge.sanitize` (safe_path, validate_filename), `uuid`, `collections.defaultdict`.

## Architecture Role

Export layer. Called by [server.py](server.md) `export_kicad` tool. See [visualization-pipeline](../concepts/visualization-pipeline.md).
