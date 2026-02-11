# SPICEBridge

AI-powered circuit design through simulation — an [MCP](https://modelcontextprotocol.io/) server that gives AI assistants direct access to SPICE circuit simulation via ngspice.

## Overview

SPICEBridge exposes 18 tools over the Model Context Protocol, covering the full circuit design workflow: template-based design with auto-calculated component values, netlist creation, simulation (AC/transient/DC), automated measurement, spec verification, and schematic generation. It works locally with Claude Code (stdio) or remotely with any MCP client via HTTP and a Cloudflare tunnel.

## The Problem

SPICE has been the gold standard for circuit simulation for decades, but getting started with it is notoriously painful. Between learning netlist syntax, memorizing analysis commands, and wrangling output data, there's a steep gap between *knowing what circuit you want* and *actually simulating it*. Professional EDA tools solve some of this with GUIs, but they're expensive, complex, and not designed for quick iteration or automation.

SPICEBridge closes that gap by letting you simulate circuits with AI. Instead of hand-writing netlists, you describe what you need in plain English — "design a 1 kHz low-pass filter" — and the AI handles the rest: picking a topology, calculating component values, running the simulation, and checking the results against your specs. It automates SPICE simulation through natural language circuit design, so you can focus on the engineering decisions instead of the syntax. Whether you want to design a filter with AI, automate repetitive simulation workflows, or build an AI agent that interacts with the physical world, SPICEBridge gives an LLM the tools to do real circuit simulation — no SPICE expertise required.

## Who This Is For

- **EE students** experimenting with AI-assisted circuit design and learning how circuits behave through fast iteration
- **Hobbyists and makers** who know what they want to build but don't want to learn SPICE syntax to get there
- **Engineers** automating repetitive simulation workflows — sweep parameters, check specs, generate reports
- **AI agent builders** who need their agents to interact with the analog/physical world through simulation
- **Educators** who want students focused on circuit concepts, not fighting tooling
- **MCP developers** looking for a full-featured reference implementation of an MCP server with templates, measurement, and design automation

## Why MCP?

The [Model Context Protocol](https://modelcontextprotocol.io/) is an open standard that lets AI assistants call external tools in a structured way. SPICEBridge implements MCP so that any compatible AI — Claude, or anything else that speaks the protocol — can run SPICE simulations directly, without custom integrations or glue code.

## Requirements

- Python >= 3.10
- [ngspice](https://ngspice.sourceforge.io/) installed and on PATH

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

### Local (Claude Code)

SPICEBridge includes a `.mcp.json` at the project root. Claude Code auto-discovers this file when you open the project, making all tools available over stdio with no manual configuration.

### Cloud (Claude.ai, remote clients)

```bash
./start_cloud.sh
```

Starts the MCP server and a named Cloudflare tunnel with a permanent URL. See [docs/cloud-setup.md](docs/cloud-setup.md) for setup details.

MCP client config:

```json
{
  "mcpServers": {
    "spicebridge": {
      "url": "https://spicebridge.clanker-lover.work/mcp"
    }
  }
}
```

## Tools

### Create & Configure

| Tool | Description |
|------|-------------|
| `create_circuit` | Store a SPICE netlist, returns a circuit ID |
| `list_templates` | List available circuit templates |
| `load_template` | Load a template with parameter substitution |
| `calculate_components` | Auto-calculate component values from specs |
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

### Evaluate & Visualize

| Tool | Description |
|------|-------------|
| `get_results` | Retrieve last simulation results |
| `compare_specs` | Check measurements against target specs |
| `draw_schematic` | Generate a schematic diagram (PNG/SVG) |

### Design Automation

| Tool | Description |
|------|-------------|
| `auto_design` | Single-call design loop: template + simulation + measurement + spec check |

## Circuit Templates

11 built-in templates with automatic component value calculation:

| Template | Type |
|----------|------|
| `rc_lowpass_1st` | 1st-order RC low-pass filter |
| `rc_highpass_1st` | 1st-order RC high-pass filter |
| `sallen_key_lowpass_2nd` | 2nd-order Sallen-Key low-pass filter |
| `sallen_key_hpf_2nd` | 2nd-order Sallen-Key high-pass filter |
| `mfb_bandpass` | Multiple feedback bandpass filter |
| `twin_t_notch` | Twin-T notch filter |
| `inverting_opamp` | Inverting amplifier |
| `summing_amplifier` | Summing amplifier |
| `differential_amp` | Differential amplifier |
| `instrumentation_amp` | Instrumentation amplifier |
| `voltage_divider` | Resistive voltage divider |

Component values are snapped to the E24 standard series.

## Example Interaction

A typical AI-driven design loop:

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

Or in a single call:

```
auto_design(template_id="rc_lowpass_1st", specs={"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}})
   -> complete results with circuit, simulation data, measurements, and pass/fail
```

## Transports

```bash
python -m spicebridge                                  # stdio (default, local)
python -m spicebridge --transport streamable-http      # HTTP (cloud)
python -m spicebridge --transport sse                  # SSE
```

## Development

```bash
pytest                  # run tests
ruff check src/ tests/  # lint
ruff format src/ tests/ # format
```

## License

GPL-3.0-or-later
