# ngspice

**Type:** External tool (circuit simulator)

## Role in SPICEBridge

ngspice is the simulation engine that SPICEBridge wraps. It reads SPICE netlists, performs circuit analysis (AC, transient, DC operating point), and produces `.raw` output files containing simulation results.

## FACTS

- Must be installed on PATH for any simulation tool to work (Source: `simulator.py`).
- Server logs a warning at startup if not found (Source: `server.py`).
- Setup wizard checks for ngspice and warns if missing (Source: `setup_wizard.py`).
- Invoked in batch mode: `ngspice -b -r <rawfile> <netlistfile>` (Source: `simulator.py`).
- 60-second timeout per simulation (Source: `simulator.py`).
- Output format: binary `.raw` files parsed by spicelib's `RawRead` (Source: `parser.py`).

## SPICE Conventions

- Node `0` is ground (required in every circuit).
- First line of netlist is always a title/comment.
- `M` suffix = milli (1e-3), `meg` = mega (1e6) -- differs from standard SI.
- Component letters: R(resistor), C(capacitor), L(inductor), V(voltage source), I(current source), D(diode), Q(BJT), M(MOSFET), X(subcircuit).

## Related Pages

- [simulator.py](../summaries/simulator.md), [parser.py](../summaries/parser.md)
- [simulation-pipeline](../concepts/simulation-pipeline.md)
- [dependencies](dependencies.md)
