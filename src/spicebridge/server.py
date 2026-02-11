"""MCP server exposing SPICEBridge tools for AI clients."""

from __future__ import annotations

import re

from mcp.server.fastmcp import FastMCP

from spicebridge.circuit_manager import CircuitManager
from spicebridge.parser import (
    parse_results,
    read_ac_at_frequency,
    read_ac_bandwidth,
)
from spicebridge.schematic import draw_schematic as _draw_schematic
from spicebridge.simulator import run_simulation, validate_netlist_syntax
from spicebridge.solver import solve as _solve_components
from spicebridge.standard_values import format_engineering, snap_to_standard
from spicebridge.template_manager import (
    TemplateManager,
    modify_component_in_netlist,
    substitute_params,
)

mcp = FastMCP("SPICEBridge")
_manager = CircuitManager()
_templates = TemplateManager()

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


@mcp.tool()
def draw_schematic(circuit_id: str, fmt: str = "png") -> dict:
    """Generate a schematic diagram from a stored circuit's netlist."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    try:
        output_file = circuit.output_dir / f"schematic.{fmt}"
        _draw_schematic(circuit.netlist, output_file, fmt=fmt)
        return {"status": "ok", "filepath": str(output_file), "format": fmt}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def list_templates(category: str | None = None) -> dict:
    """List available circuit templates, optionally filtered by category."""
    templates = _templates.list_templates(category=category)
    return {"status": "ok", "templates": templates, "count": len(templates)}


@mcp.tool()
def load_template(
    template_id: str,
    params: dict | None = None,
    specs: dict | None = None,
) -> dict:
    """Load a circuit template and create a circuit from it.

    If *specs* is provided, the design-equation solver runs automatically,
    results are snapped to E24 standard values, and the netlist `.param`
    lines are updated.  Explicit *params* override solver-calculated values.
    """
    try:
        t = _templates.get_template(template_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    netlist = t.netlist
    calculated_values: dict[str, str] | None = None
    solver_notes: list[str] | None = None

    if specs is not None:
        try:
            solver_result = _solve_components(template_id, specs)
        except ValueError as exc:
            if "Unknown topology" in str(exc):
                # Template exists but has no solver — fall back to defaults
                solver_notes = [
                    f"No solver for '{template_id}'; using template defaults."
                ]
            else:
                return {"status": "error", "error": str(exc)}
        else:
            solver_params: dict[str, str] = {}
            for name, raw_val in solver_result["components"].items():
                if raw_val in ("open", "0"):
                    solver_params[name] = raw_val
                else:
                    numeric = _parse_spice_value(str(raw_val))
                    snapped = snap_to_standard(numeric, "E24")
                    solver_params[name] = format_engineering(snapped)
            calculated_values = dict(solver_params)
            solver_notes = solver_result.get("notes", [])

            # Explicit params override solver values
            if params:
                solver_params.update(params)

            netlist = substitute_params(netlist, solver_params)
            params = None  # already applied

    if params:
        netlist = substitute_params(netlist, params)

    circuit_id = _manager.create(netlist)
    preview_lines = netlist.strip().splitlines()[:5]
    result = {
        "status": "ok",
        "circuit_id": circuit_id,
        "preview": preview_lines,
        "num_lines": len(netlist.strip().splitlines()),
        "components": t.components,
        "design_equations": t.design_equations,
    }
    if calculated_values is not None:
        result["calculated_values"] = calculated_values
    if solver_notes is not None:
        result["solver_notes"] = solver_notes
    return result


@mcp.tool()
def modify_component(circuit_id: str, component: str, value: str) -> dict:
    """Modify a component value in a stored circuit's netlist."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    try:
        new_netlist = modify_component_in_netlist(circuit.netlist, component, value)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    _manager.update_netlist(circuit_id, new_netlist)
    preview_lines = new_netlist.strip().splitlines()[:5]
    return {
        "status": "ok",
        "circuit_id": circuit_id,
        "preview": preview_lines,
        "num_lines": len(new_netlist.strip().splitlines()),
    }


@mcp.tool()
def validate_netlist(circuit_id: str) -> dict:
    """Validate the netlist syntax of a stored circuit using ngspice."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    prepared = _prepare_netlist(circuit.netlist, ".op")
    try:
        valid, errors = validate_netlist_syntax(prepared)
    except RuntimeError as e:
        return {"status": "error", "error": str(e)}

    return {"status": "ok", "valid": valid, "errors": errors}


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _require_results(circuit_id: str, analysis_type: str) -> tuple | dict:
    """Validate circuit exists, has results, and results match analysis type.

    Returns (circuit, results) on success, or an error dict on failure.
    """
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    results = circuit.last_results
    if results is None:
        return {
            "status": "error",
            "error": "No simulation results — run an analysis first",
        }

    if results.get("analysis_type") != analysis_type:
        return {
            "status": "error",
            "error": (
                f"Expected {analysis_type} results but found "
                f"'{results.get('analysis_type')}'"
            ),
        }

    return (circuit, results)


_SOURCE_DC_RE = re.compile(
    r"^\s*(v\w+)\s+\S+\s+\S+\s+(?:dc\s+)?(\S+)",
    re.IGNORECASE | re.MULTILINE,
)


def _get_source_voltage(netlist: str, source_name: str) -> float | None:
    """Parse netlist text to extract DC voltage of a named voltage source."""
    for m in _SOURCE_DC_RE.finditer(netlist):
        if m.group(1).lower() == source_name.lower():
            try:
                return _parse_spice_value(m.group(2))
            except ValueError:
                return None
    return None


def _parse_spice_value(s: str) -> float:
    """Convert a SPICE value string (e.g., '1k', '100n') to a float."""
    suffixes = {
        "t": 1e12,
        "g": 1e9,
        "meg": 1e6,
        "k": 1e3,
        "m": 1e-3,
        "u": 1e-6,
        "n": 1e-9,
        "p": 1e-12,
        "f": 1e-15,
    }
    s = s.strip().lower()
    for suffix, mult in sorted(suffixes.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    return float(s)


# Mapping from user-facing spec names to (analysis_type, result_key)
_SPEC_MAP: dict[str, tuple[str, str]] = {
    "f_3dB_hz": ("AC Analysis", "f_3dB_hz"),
    "gain_dc_dB": ("AC Analysis", "gain_dc_dB"),
    "rolloff_dB_per_decade": ("AC Analysis", "rolloff_rate_dB_per_decade"),
    "rolloff_rate_dB_per_decade": ("AC Analysis", "rolloff_rate_dB_per_decade"),
    "peak_gain_dB": ("AC Analysis", "peak_gain_dB"),
    "phase_at_f3dB_deg": ("AC Analysis", "phase_at_f3dB_deg"),
    "steady_state_value": ("Transient Analysis", "steady_state_value"),
    "rise_time_10_90_s": ("Transient Analysis", "rise_time_10_90_s"),
    "overshoot_pct": ("Transient Analysis", "overshoot_pct"),
    "settling_time_1pct_s": ("Transient Analysis", "settling_time_1pct_s"),
}


def _extract_spec_value(results: dict, spec_name: str) -> float | None:
    """Look up a value from results using _SPEC_MAP with DC OP node fallback."""
    if spec_name in _SPEC_MAP:
        _, key = _SPEC_MAP[spec_name]
        return results.get(key)

    # DC OP node fallback: try direct lookup then case-insensitive
    nodes = results.get("nodes", {})
    if spec_name in nodes:
        return nodes[spec_name]
    for k, v in nodes.items():
        if k.lower() == spec_name.lower():
            return v
    return None


def _check_spec(actual: float | None, spec_def: dict) -> tuple[bool, dict]:
    """Evaluate a single spec against actual value.

    spec_def can contain:
      - {"target": N, "tolerance_pct": P}  — passes if within P% of N
      - {"min": N, "max": M}               — passes if min <= actual <= max
      - {"min": N}                          — passes if actual >= min
      - {"max": M}                          — passes if actual <= max
      - {"target": N}                       — passes if within 1% of N
    """
    detail: dict = {"actual": actual}

    if actual is None:
        detail["error"] = "Value not available in results"
        return False, detail

    if "target" in spec_def:
        target = spec_def["target"]
        tol_pct = spec_def.get("tolerance_pct", 1.0)
        margin = abs(target) * tol_pct / 100.0 if target != 0 else tol_pct / 100.0
        passed = abs(actual - target) <= margin
        detail.update({"target": target, "tolerance_pct": tol_pct, "margin": margin})
        return passed, detail

    passed = True
    if "min" in spec_def:
        detail["min"] = spec_def["min"]
        if actual < spec_def["min"]:
            passed = False
    if "max" in spec_def:
        detail["max"] = spec_def["max"]
        if actual > spec_def["max"]:
            passed = False

    return passed, detail


# ---------------------------------------------------------------------------
# Measurement tools
# ---------------------------------------------------------------------------


@mcp.tool()
def measure_bandwidth(circuit_id: str, threshold_db: float = -3.0) -> dict:
    """Measure the bandwidth (cutoff frequency) of an AC analysis result.

    Uses -3dB by default; specify threshold_db for custom cutoff levels.
    """
    if threshold_db >= 0:
        return {"status": "error", "error": "threshold_db must be negative"}

    check = _require_results(circuit_id, "AC Analysis")
    if isinstance(check, dict):
        return check
    circuit, results = check

    if threshold_db == -3.0:
        return {
            "status": "ok",
            "f_cutoff_hz": results.get("f_3dB_hz"),
            "rolloff_db_per_decade": results.get("rolloff_rate_dB_per_decade"),
            "threshold_db": threshold_db,
        }

    raw_path = circuit.output_dir / "circuit.raw"
    try:
        bw = read_ac_bandwidth(raw_path, threshold_db)
    except Exception as e:
        return {"status": "error", "error": str(e)}

    return {
        "status": "ok",
        "f_cutoff_hz": bw["f_cutoff_hz"],
        "rolloff_db_per_decade": bw["rolloff_db_per_decade"],
        "threshold_db": threshold_db,
    }


@mcp.tool()
def measure_gain(circuit_id: str, frequency_hz: float) -> dict:
    """Measure gain and phase at a specific frequency from AC analysis results."""
    if frequency_hz <= 0:
        return {"status": "error", "error": "frequency_hz must be positive"}

    check = _require_results(circuit_id, "AC Analysis")
    if isinstance(check, dict):
        return check
    circuit, _results = check

    raw_path = circuit.output_dir / "circuit.raw"
    try:
        data = read_ac_at_frequency(raw_path, frequency_hz)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    return {
        "status": "ok",
        "frequency_hz": frequency_hz,
        "gain_db": data["gain_db"],
        "phase_deg": data["phase_deg"],
    }


@mcp.tool()
def measure_dc(circuit_id: str, node_name: str) -> dict:
    """Measure the DC voltage at a specific node from operating point results."""
    check = _require_results(circuit_id, "Operating Point")
    if isinstance(check, dict):
        return check
    _circuit, results = check

    nodes = results.get("nodes", {})

    # Direct lookup
    if node_name in nodes:
        return {"status": "ok", "node_name": node_name, "voltage_V": nodes[node_name]}

    # Case-insensitive fallback
    for k, v in nodes.items():
        if k.lower() == node_name.lower():
            return {"status": "ok", "node_name": k, "voltage_V": v}

    return {
        "status": "error",
        "error": f"Node '{node_name}' not found. Available nodes: {list(nodes.keys())}",
    }


@mcp.tool()
def measure_transient(circuit_id: str) -> dict:
    """Extract key transient response metrics (rise time, settling time, overshoot)."""
    check = _require_results(circuit_id, "Transient Analysis")
    if isinstance(check, dict):
        return check
    _circuit, results = check

    rise_s = results.get("rise_time_10_90_s")
    settling_s = results.get("settling_time_1pct_s")

    return {
        "status": "ok",
        "rise_time_us": rise_s * 1e6 if rise_s is not None else None,
        "settling_time_us": settling_s * 1e6 if settling_s is not None else None,
        "overshoot_pct": results.get("overshoot_pct"),
        "steady_state_V": results.get("steady_state_value"),
    }


@mcp.tool()
def measure_power(circuit_id: str) -> dict:
    """Measure power consumption from DC operating point results."""
    check = _require_results(circuit_id, "Operating Point")
    if isinstance(check, dict):
        return check
    circuit, results = check

    nodes = results.get("nodes", {})
    per_source: dict[str, dict] = {}
    total_power = 0.0

    for key, current in nodes.items():
        # ngspice branch current traces: i(v1) or v1#branch
        source_name = None
        if key.startswith("i(") and key.endswith(")"):
            source_name = key[2:-1]
        elif key.endswith("#branch"):
            source_name = key[: -len("#branch")]

        if source_name is None:
            continue

        voltage = _get_source_voltage(circuit.netlist, source_name)
        if voltage is None:
            continue

        # Power = -V * I (ngspice convention)
        power_w = -voltage * current
        per_source[source_name] = {
            "current_A": current,
            "voltage_V": voltage,
            "power_mW": power_w * 1e3,
        }
        total_power += power_w

    return {
        "status": "ok",
        "total_power_mW": total_power * 1e3,
        "per_source": per_source,
    }


@mcp.tool()
def compare_specs(circuit_id: str, specs: dict) -> dict:
    """Compare simulation results against design specifications.

    specs format: {"spec_name": {"target": N, "tolerance_pct": P}}
    or {"spec_name": {"min": N, "max": M}}.
    """
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    results = circuit.last_results
    if results is None:
        return {
            "status": "error",
            "error": "No simulation results — run an analysis first",
        }

    all_passed = True
    spec_results: dict[str, dict] = {}

    for spec_name, spec_def in specs.items():
        actual = _extract_spec_value(results, spec_name)
        passed, detail = _check_spec(actual, spec_def)
        detail["passed"] = passed
        spec_results[spec_name] = detail
        if not passed:
            all_passed = False

    return {
        "status": "ok",
        "all_passed": all_passed,
        "results": spec_results,
    }


@mcp.tool()
def calculate_components(topology_id: str, specs: dict) -> dict:
    """Calculate component values for a circuit topology from target specs."""
    try:
        result = _solve_components(topology_id, specs)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# auto_design — single-call design loop
# ---------------------------------------------------------------------------

# Maps compare_specs keys → solver parameter names
_SPEC_TO_SOLVER: dict[str, str] = {
    "f_3dB_hz": "f_cutoff_hz",
    "f_cutoff_hz": "f_cutoff_hz",
    "f_center_hz": "f_center_hz",
    "f_notch_hz": "f_notch_hz",
}

# Sensible defaults per simulation type
_DEFAULT_SIM_PARAMS: dict[str, dict] = {
    "ac": {"start_freq": 1, "stop_freq": 1e6, "points_per_decade": 20},
    "transient": {"stop_time": 10e-3, "step_time": 10e-6},
    "dc": {},
}


def _specs_to_solver_params(specs: dict) -> dict:
    """Extract target values from compare_specs format and map to solver keys.

    Input:  {"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}}
    Output: {"f_cutoff_hz": 1000}
    """
    solver_params: dict = {}
    for spec_key, spec_def in specs.items():
        if spec_key in _SPEC_TO_SOLVER:
            target = spec_def.get("target") if isinstance(spec_def, dict) else spec_def
            if target is not None:
                solver_params[_SPEC_TO_SOLVER[spec_key]] = target
    return solver_params


def _run_sim(circuit_id: str, sim_type: str, sim_params: dict | None) -> dict:
    """Merge user sim_params with defaults and dispatch to the right analysis."""
    defaults = _DEFAULT_SIM_PARAMS.get(sim_type, {})
    merged = {**defaults, **(sim_params or {})}

    if sim_type == "ac":
        return run_ac_analysis(circuit_id, **merged)
    elif sim_type == "transient":
        return run_transient(circuit_id, **merged)
    elif sim_type == "dc":
        return run_dc_op(circuit_id)
    else:
        return {"status": "error", "error": f"Unknown sim_type '{sim_type}'"}


def _collect_measurements(circuit_id: str, sim_type: str, specs: dict) -> dict:
    """Run relevant measure_* tools and collect results. Failures are silenced."""
    import contextlib

    measurements: dict = {}

    with contextlib.suppress(Exception):
        if sim_type == "ac":
            measurements["bandwidth"] = measure_bandwidth(circuit_id)
        elif sim_type == "transient":
            measurements["transient"] = measure_transient(circuit_id)
        elif sim_type == "dc":
            for spec_key in specs:
                # Node-voltage specs like "v(out)"
                if spec_key not in _SPEC_MAP:
                    with contextlib.suppress(Exception):
                        measurements[spec_key] = measure_dc(circuit_id, spec_key)
            with contextlib.suppress(Exception):
                measurements["power"] = measure_power(circuit_id)

    return measurements


@mcp.tool()
def auto_design(
    template_id: str,
    specs: dict,
    sim_type: str = "ac",
    sim_params: dict | None = None,
) -> dict:
    """Run the full design loop in one call: load template, simulate, and verify.

    *specs* uses compare_specs format:
        {"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}}

    *sim_type* is one of "ac", "transient", or "dc".
    *sim_params* optionally overrides default simulation parameters.

    Returns accumulated results including circuit_id, simulation data,
    measurements, and spec comparison.  On failure at any step, returns
    partial results with an ``error`` key and ``failed_step``.
    """
    result: dict = {}

    # 1. Translate specs to solver format
    solver_specs = _specs_to_solver_params(specs)

    # 2. Load template (with solver specs if any mapped)
    load_args: dict = {"template_id": template_id}
    if solver_specs:
        load_args["specs"] = solver_specs
    loaded = load_template(**load_args)
    if loaded.get("status") != "ok":
        return {**result, **loaded, "failed_step": "load_template"}
    result["circuit_id"] = loaded["circuit_id"]
    result["netlist_preview"] = loaded.get("preview", [])
    result["calculated_values"] = loaded.get("calculated_values")
    result["solver_notes"] = loaded.get("solver_notes")

    circuit_id = loaded["circuit_id"]

    # 3. Validate netlist
    validation = validate_netlist(circuit_id)
    if validation.get("status") != "ok" or not validation.get("valid", False):
        return {
            **result,
            "validation": validation,
            "failed_step": "validate_netlist",
            "status": "error",
            "error": "Netlist validation failed",
        }

    # 4. Simulate
    sim_result = _run_sim(circuit_id, sim_type, sim_params)
    result["simulation"] = sim_result
    if sim_result.get("status") != "ok":
        return {
            **result,
            "failed_step": "simulation",
            "status": "error",
            "error": sim_result.get("error", "Simulation failed"),
        }

    # 5. Collect measurements
    result["measurements"] = _collect_measurements(circuit_id, sim_type, specs)

    # 6. Compare specs
    comparison = compare_specs(circuit_id, specs)
    result["comparison"] = comparison
    result["all_specs_passed"] = comparison.get("all_passed", False)
    result["status"] = "ok"

    return result


def configure_for_remote() -> None:
    """Disable DNS rebinding protection for tunnel/remote access."""
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )


if __name__ == "__main__":
    mcp.run()
