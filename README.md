# SPICEBridge

AI-powered circuit design through simulation.

**Status:** Early development

## Requirements

- Python >= 3.10
- [ngspice](https://ngspice.sourceforge.io/) installed and on PATH

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## MCP Server

Start the server manually:

```bash
python -m spicebridge.server
```

The server exposes 6 tools over stdio transport:

| Tool | Description |
|------|-------------|
| `create_circuit` | Store a SPICE netlist, returns a circuit ID |
| `run_ac_analysis` | AC frequency sweep on a stored circuit |
| `run_transient` | Transient (time-domain) analysis |
| `run_dc_op` | DC operating point analysis |
| `get_results` | Retrieve last simulation results |
| `draw_schematic` | Generate a schematic diagram (PNG/SVG) from the netlist |

## Connecting with Claude Code

SPICEBridge includes a `.mcp.json` at the project root. Claude Code auto-discovers
this file when you open the project, making all 6 tools available without manual
configuration. The config uses stdio transport pointing to the project virtualenv.

## Example Interaction

A typical AI-driven circuit design loop:

```
1. create_circuit  — submit an RC low-pass filter netlist
   -> circuit_id: "a1b2c3d4"

2. run_ac_analysis — sweep 1 Hz to 1 MHz, 10 points/decade
   -> f_3dB_hz: 1592, gain_at_f3dB_dB: -3.01

3. get_results     — retrieve the stored analysis results
   -> same structured data as step 2

4. draw_schematic  — generate a PNG schematic
   -> filepath: "/tmp/spicebridge_.../schematic.png"
```

## License

GPL-3.0-or-later
