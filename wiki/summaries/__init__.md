# __init__.py

**Source:** `src/spicebridge/__init__.py`

## Purpose

Package init with lazy imports. Defines `__version__ = "1.0.0"` (note: `pyproject.toml` declares version `1.3.0` -- see [Open Questions](#open-questions)).

## Key Mechanism

Uses `__getattr__` with a `_LAZY_IMPORTS` dict to defer heavy imports until first attribute access. Exposed names: `run_simulation`, `parse_results`, `read_ac_at_frequency`, `read_ac_bandwidth`, `parse_netlist`, `draw_schematic`, `export_kicad_schematic`, `TemplateManager`, `generate_model`, `GeneratedModel`, `ModelStore`, `render_svg`, `start_viewer`.

## Open Questions

- `__version__` is `"1.0.0"` but `pyproject.toml` says `1.3.0`. Which is canonical? The pyproject.toml version is what pip uses for distribution.
