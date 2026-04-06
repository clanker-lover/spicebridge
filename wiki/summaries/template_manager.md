# template_manager.py

**Source:** `src/spicebridge/template_manager.py`

## Purpose

Manages the circuit template library -- discovers, loads, and serves pre-built circuit templates from JSON files. Also provides netlist parameter substitution helpers.

## Public API

- **`TemplateManager`**: Main class. Lazy-loads templates on first access.
  - `list_templates(category=None)`: Returns summary dicts of all templates.
  - `get_template(template_id)`: Returns a `Template` by ID. Raises `KeyError` if not found.
  - `reload()`: Force-reloads all templates.
- **`Template`** dataclass: `id`, `name`, `category`, `description`, `design_equations`, `netlist`, `components`, `source` ("built-in"/"user"), `ports`.
- **`substitute_params(netlist, params)`**: Rewrites `.param` lines in a netlist with new values.
- **`modify_component_in_netlist(netlist, component, value)`**: Modifies a component value -- tries `.param` lines first, then instance lines.

## Template Sources

1. **Built-in**: `src/spicebridge/templates/*.json` (11 templates shipped with the package).
2. **User**: `~/.spicebridge/templates/*.json` (user templates override built-ins by ID). Symlinks are rejected for security.

## Template JSON Schema

Each JSON file must contain: `id`, `name`, `category`, `description`, `netlist`, and optionally `design_equations`, `components`, `ports`.

## Dependencies

`spicebridge.sanitize.validate_component_value`, `importlib.resources`, `json`, `re`.

## Architecture Role

Template layer between solver and simulation. Called by [server.py](server.md). See [template-system](../concepts/template-system.md).
