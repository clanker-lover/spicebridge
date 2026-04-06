# server.py

**Source:** `src/spicebridge/server.py`

## Purpose

Central MCP server module that exposes all 28 SPICEBridge tools to AI clients via the FastMCP framework. This is the application's main integration point -- it imports every other module and wires them together as MCP tool endpoints.

## Public API (MCP Tools)

**Create & Configure:** `create_circuit`, `delete_circuit`, `list_templates`, `load_template`, `calculate_components`, `modify_component`, `validate_netlist`

**Simulate:** `run_ac_analysis`, `run_transient`, `run_dc_op`

**Measure:** `measure_bandwidth`, `measure_gain`, `measure_dc`, `measure_transient`, `measure_power`

**Evaluate & Export:** `get_results`, `compare_specs`, `draw_schematic`, `export_kicad`, `open_viewer`

**Composition:** `set_ports`, `get_ports`, `connect_stages`

**Advanced:** `run_monte_carlo`, `run_worst_case`, `auto_design`

**Models:** `create_model`, `list_models`

## Key Types/Patterns

- **`mcp`**: `FastMCP` instance, the server object. Configured with instructions directing AI clients to always share schematic URLs.
- **`_monitored` decorator**: Wraps every tool function with metrics recording, RPM throttling, and error logging. See [metrics](metrics.md).
- **`_http_transport` flag**: Boolean toggled by `configure_for_remote()` to adjust behavior for cloud deployment (strips SVG content, enforces rate limits).
- **Resource limits**: `_MAX_NETLIST_SIZE` (100KB), `_MAX_STAGES` (20), `_MAX_MONTE_CARLO_RUNS` (100), `_MAX_WORST_CASE_COMPONENTS` (20).

## Custom HTTP Routes

- `/favicon.ico` -- serves bundled logo as PNG
- `/schematics/{circuit_id}.png` -- serves cached schematic images
- `/health` -- metrics endpoint, protected by `SPICEBRIDGE_HEALTH_TOKEN`

## Dependencies

Imports from every other spicebridge module. External: `mcp` (FastMCP), `starlette`, `cairosvg`, `base64`, `json`.

## Architecture Role

Sole entry point for all AI-client interactions. Delegates all domain logic to specialized modules. See [mcp-tool-architecture](../concepts/mcp-tool-architecture.md), [simulation-pipeline](../concepts/simulation-pipeline.md).
