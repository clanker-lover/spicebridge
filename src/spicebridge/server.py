"""MCP server exposing SPICEBridge tools for AI clients."""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP

from spicebridge.circuit_manager import CircuitManager
from spicebridge.parser import parse_results
from spicebridge.simulator import run_simulation

mcp = FastMCP("SPICEBridge")
_manager = CircuitManager()

# Patterns for analysis commands to strip (case-insensitive)
_ANALYSIS_RE = re.compile(r"^\s*\.(ac|tran|op|dc)\b", re.IGNORECASE)
_END_RE = re.compile(r"^\s*\.end\s*$", re.IGNORECASE)


def _prepare_netlist(netlist: str, analysis_line: str) -> str:
    """Strip existing analysis/.end commands and append new ones."""
    lines = []
    for line in netlist.splitlines():
        if _ANALYSIS_RE.match(line):
            continue
        if _END_RE.match(line):
            continue
        lines.append(line)
    lines.append(analysis_line)
    lines.append(".end")
    return "\n".join(lines) + "\n"


@mcp.tool()
def create_circuit(netlist: str) -> dict:
    """Store a SPICE netlist and return a circuit ID for subsequent analyses."""
    circuit_id = _manager.create(netlist)
    preview_lines = netlist.strip().splitlines()[:5]
    return {
        "status": "ok",
        "circuit_id": circuit_id,
        "preview": preview_lines,
        "num_lines": len(netlist.strip().splitlines()),
    }


@mcp.tool()
def run_ac_analysis(
    circuit_id: str,
    start_freq: float = 1.0,
    stop_freq: float = 1e6,
    points_per_decade: int = 10,
) -> dict:
    """Run AC analysis on a stored circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    analysis_line = f".ac dec {points_per_decade} {start_freq} {stop_freq}"
    prepared = _prepare_netlist(circuit.netlist, analysis_line)

    try:
        success = run_simulation(prepared, output_dir=circuit.output_dir)
        if not success:
            return {"status": "error", "error": "Simulation produced no output"}
        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        _manager.update_results(circuit_id, results)
        return {"status": "ok", "results": results}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def run_transient(
    circuit_id: str,
    stop_time: float,
    step_time: float,
    startup_time: float | None = None,
) -> dict:
    """Run transient analysis on a stored circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    if startup_time is not None:
        analysis_line = f".tran {step_time} {stop_time} {startup_time}"
    else:
        analysis_line = f".tran {step_time} {stop_time}"

    prepared = _prepare_netlist(circuit.netlist, analysis_line)

    try:
        success = run_simulation(prepared, output_dir=circuit.output_dir)
        if not success:
            return {"status": "error", "error": "Simulation produced no output"}
        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        _manager.update_results(circuit_id, results)
        return {"status": "ok", "results": results}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def run_dc_op(circuit_id: str) -> dict:
    """Run DC operating point analysis on a stored circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    prepared = _prepare_netlist(circuit.netlist, ".op")

    try:
        success = run_simulation(prepared, output_dir=circuit.output_dir)
        if not success:
            return {"status": "error", "error": "Simulation produced no output"}
        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        _manager.update_results(circuit_id, results)
        return {"status": "ok", "results": results}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def get_results(circuit_id: str) -> dict:
    """Return the last simulation results for a circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    return {
        "status": "ok",
        "results": circuit.last_results,
    }


if __name__ == "__main__":
    mcp.run()
