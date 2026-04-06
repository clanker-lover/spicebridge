# Template System

Cross-cutting concept appearing in: `template_manager.py`, `solver.py`, `standard_values.py`, `server.py`, `templates/*.json`

## FACTS

The template system is a three-layer stack enabling one-call circuit design (Source: `server.py` `load_template` tool):

1. **Template data** -- 11 JSON files in `src/spicebridge/templates/` define circuit netlists with `.param` placeholders, design equations, and port mappings (Source: template JSON files).
2. **Solver layer** -- `solver.py` contains 12 topology-specific design equation solvers that calculate component values from target specs (Source: `solver.py`).
3. **Standard values** -- `standard_values.py` snaps calculated values to real-world E-series component values (E12/E24/E96) (Source: `standard_values.py`).

## One-Call Design Flow (`load_template` with specs)

When `specs` is provided to `load_template` (Source: `server.py`):
1. Solver runs: `_solve_components(template_id, specs)` -> raw component values.
2. Values are snapped to E24 series via `snap_to_standard()`.
3. Snapped values are formatted with `format_engineering()` -> e.g., `"15.9k"`.
4. `.param` lines in the netlist are updated via `substitute_params()`.
5. Any explicit `params` override solver values.
6. The netlist is sanitized and a circuit is created.

## Template Discovery

Templates are loaded from two sources (Source: `template_manager.py`):
- Built-in: `importlib.resources.files("spicebridge") / "templates"` -- shipped with the package.
- User: `~/.spicebridge/templates/` -- user-created templates override built-ins by ID. Symlinks are rejected.

## INFERENCES

The `.param` approach is elegant -- it lets templates be complete valid SPICE netlists (testable standalone) while still allowing programmatic value substitution. The solver's `_pick_c_anchor()` strategy of trying multiple capacitor values to find one that puts the resistor in a practical range is a pragmatic engineering heuristic.

## OPEN QUESTIONS

- `noninverting_opamp` has a solver but no template. Why not add one? Possibly because the non-inverting topology requires connecting the opamp differently than the existing subcircuit.

## Related Pages

- [template_manager.py](../summaries/template_manager.md), [solver.py](../summaries/solver.md), [standard_values.py](../summaries/standard_values.md)
- [templates JSON](../summaries/templates_json.md)
