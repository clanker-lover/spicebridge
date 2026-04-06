# KiCad

**Type:** External tool (EDA software)

## Role in SPICEBridge

KiCad is an open-source PCB design suite. SPICEBridge can export circuit designs as KiCad 8 schematic files (`.kicad_sch`) for downstream PCB layout work.

## FACTS

- Export via `export_kicad` tool or `export_kicad_schematic()` function (Source: `kicad_export.py`).
- Output format: KiCad 8 S-expression syntax (`.kicad_sch` files) (Source: `kicad_export.py`).
- Maps SPICE components to KiCad library symbols (e.g., R -> `Device:R`, Q -> `Device:Q_NPN_BCE`) (Source: `kicad_export.py`).
- Uses KiCad's 2.54mm grid for component placement (Source: `kicad_export.py`).
- Contains embedded lib_symbol templates for standard components (Source: `kicad_export.py`).

## Related Pages

- [kicad_export.py](../summaries/kicad_export.md)
- [visualization-pipeline](../concepts/visualization-pipeline.md)
