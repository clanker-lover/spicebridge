# SPICEBridge

AI-powered circuit design through simulation — an [MCP](https://modelcontextprotocol.io/) server that gives language models direct access to SPICE circuit simulation via ngspice. Describe a circuit in plain English and let the AI handle netlist generation, simulation, measurement, and spec verification.

## Features

- **28 tools** covering the full circuit design workflow
- **11 built-in templates** with automatic component value calculation (E24 series)
- **Simulation**: AC sweep, transient, DC operating point
- **Measurement**: bandwidth, gain, DC levels, transient metrics, power
- **Monte Carlo & worst-case analysis** under component tolerances
- **Multi-stage composition** — connect circuit stages with automatic port mapping
- **Model wizard** — generate SPICE `.lib` models from datasheet parameters
- **KiCad export** — output `.kicad_sch` schematics
- **Web viewer** — interactive schematic viewer in the browser
- **Spec verification** — compare results against design targets

## Install

```bash
pip install spicebridge
```

## Requirements

- Python 3.12+
- [ngspice](https://ngspice.sourceforge.io/) installed and on PATH

## Quick Start

### Local (Claude Code / stdio)

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "spicebridge": {
      "command": "spicebridge"
    }
  }
}
```

### Cloud (Streamable HTTP)

```bash
spicebridge --transport streamable-http --port 8000
```

## Example

```
1. load_template("rc_lowpass_1st", specs={"f_3dB_hz": 1000})
   -> netlist with R=1.6k, C=100nF, circuit_id: "a1b2c3d4"

2. run_ac_analysis(circuit_id, start_freq=1, stop_freq=1e6)
   -> frequency response data

3. measure_bandwidth(circuit_id)
   -> f_3dB_hz: 995

4. compare_specs(circuit_id, specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}})
   -> PASS

5. draw_schematic(circuit_id)
   -> schematic PNG
```

## Tools

### Create & Configure

| Tool | Description |
|------|-------------|
| `create_circuit` | Store a SPICE netlist, returns a circuit ID |
| `delete_circuit` | Delete a stored circuit and clean up resources |
| `list_templates` | List available circuit templates |
| `load_template` | Load a template with parameter substitution |
| `calculate_components` | Calculate component values from target specs |
| `modify_component` | Change a component value in a stored circuit |
| `validate_netlist` | Check a netlist for errors before simulation |

### Simulate

| Tool | Description |
|------|-------------|
| `run_ac_analysis` | AC frequency sweep |
| `run_transient` | Transient (time-domain) analysis |
| `run_dc_op` | DC operating point analysis |

### Measure

| Tool | Description |
|------|-------------|
| `measure_bandwidth` | Find -3 dB bandwidth from AC results |
| `measure_gain` | Measure gain at a specific frequency |
| `measure_dc` | Extract DC operating point values |
| `measure_transient` | Measure time-domain characteristics |
| `measure_power` | Calculate power dissipation |

### Evaluate & Export

| Tool | Description |
|------|-------------|
| `get_results` | Retrieve last simulation results |
| `compare_specs` | Check measurements against target specs |
| `draw_schematic` | Generate a schematic diagram (PNG/SVG) |
| `export_kicad` | Export as KiCad 8 schematic (.kicad_sch) |
| `open_viewer` | Start the interactive web schematic viewer |

### Composition & Ports

| Tool | Description |
|------|-------------|
| `set_ports` | Define port-to-node mappings for a circuit |
| `get_ports` | Return port definitions (auto-detect if unset) |
| `connect_stages` | Compose multiple stages into a single circuit |

### Advanced Analysis

| Tool | Description |
|------|-------------|
| `run_monte_carlo` | Monte Carlo analysis under component tolerances |
| `run_worst_case` | Worst-case analysis at tolerance extremes |
| `auto_design` | Full design loop in one call: template + simulate + verify |

### Model Management

| Tool | Description |
|------|-------------|
| `create_model` | Generate a SPICE .lib model from datasheet parameters |
| `list_models` | List all saved models in the model library |

## Development

```bash
git clone https://github.com/clanker-lover/spicebridge.git
cd spicebridge
pip install -e ".[dev]"
pytest
```

## License

GPL-3.0-or-later
