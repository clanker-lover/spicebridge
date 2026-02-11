"""Monte Carlo and worst-case analysis infrastructure."""

from __future__ import annotations

import itertools
import random
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from spicebridge.parser import parse_results
from spicebridge.simulator import run_simulation
from spicebridge.standard_values import format_engineering, parse_spice_value

# Patterns for analysis commands to strip (same as server.py)
_ANALYSIS_RE = re.compile(r"^\s*\.(ac|tran|op|dc)\b", re.IGNORECASE)
_END_RE = re.compile(r"^\s*\.end\s*$", re.IGNORECASE)

# Instance line: R1 node1 node2 <value> [optional stuff like IC=0]
_INSTANCE_RE = re.compile(r"^\s*([RCL]\w+)\s+\S+\s+\S+\s+(\S+)", re.IGNORECASE)

# .param line: .param R1 = 1k  or  .param R1=1k
_PARAM_RE = re.compile(
    r"^\s*\.param\s+([RCL]\w+)\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE
)


@dataclass
class ComponentInfo:
    ref: str  # e.g. "R1"
    value: float  # parsed numeric value
    value_str: str  # original string e.g. "1k"
    line_num: int  # 0-based line number
    source: str  # "param" or "instance"


def parse_component_values(netlist: str) -> list[ComponentInfo]:
    """Extract R/C/L component values from a netlist.

    Pass 1: scan .param lines for R/C/L keys.
    Pass 2: scan instance lines R1 node1 node2 <value>.
    Dedup: .param wins over instance.
    Skips parameterized values (containing '{').
    """
    components: dict[str, ComponentInfo] = {}
    lines = netlist.splitlines()

    # Pass 1: .param lines
    for i, line in enumerate(lines):
        m = _PARAM_RE.match(line)
        if m:
            ref = m.group(1).upper()
            val_str = m.group(2)
            if "{" in val_str:
                continue
            try:
                value = parse_spice_value(val_str)
            except (ValueError, IndexError):
                continue
            components[ref] = ComponentInfo(
                ref=ref, value=value, value_str=val_str, line_num=i, source="param"
            )

    # Pass 2: instance lines
    for i, line in enumerate(lines):
        m = _INSTANCE_RE.match(line)
        if m:
            ref = m.group(1).upper()
            if ref in components:
                continue  # .param wins
            val_str = m.group(2)
            if "{" in val_str:
                continue
            try:
                value = parse_spice_value(val_str)
            except (ValueError, IndexError):
                continue
            components[ref] = ComponentInfo(
                ref=ref, value=value, value_str=val_str, line_num=i, source="instance"
            )

    return list(components.values())


def _resolve_tolerance(
    ref: str,
    tolerances: dict | None,
    default_tol: float,
) -> float:
    """Look up tolerance for a component: exact ref > type prefix > default."""
    if tolerances:
        # Exact ref match (case-insensitive keys)
        tol_lower = {k.lower(): v for k, v in tolerances.items()}
        if ref.lower() in tol_lower:
            return float(tol_lower[ref.lower()])
        # Type prefix match (R, C, L)
        prefix = ref[0].upper()
        if prefix.lower() in tol_lower:
            return float(tol_lower[prefix.lower()])
        if prefix.upper() in tol_lower:
            return float(tol_lower[prefix.upper()])
    return default_tol


def randomize_values(
    components: list[ComponentInfo],
    tolerances: dict | None,
    default_tol: float,
    rng: random.Random,
) -> dict[str, float]:
    """Generate random component values using Gaussian distribution (3-sigma)."""
    values: dict[str, float] = {}
    for comp in components:
        tol_pct = _resolve_tolerance(comp.ref, tolerances, default_tol)
        sigma = tol_pct / 100.0 / 3.0
        factor = 1.0 + rng.gauss(0, sigma)
        values[comp.ref] = comp.value * factor
    return values


def apply_corner(
    components: list[ComponentInfo],
    tolerances: dict | None,
    default_tol: float,
    corner: tuple[int, ...],
) -> dict[str, float]:
    """Apply a deterministic corner: direction is +1, -1, or 0."""
    values: dict[str, float] = {}
    for comp, direction in zip(components, corner, strict=False):
        tol_pct = _resolve_tolerance(comp.ref, tolerances, default_tol)
        values[comp.ref] = comp.value * (1.0 + direction * tol_pct / 100.0)
    return values


def substitute_values(
    netlist: str,
    components: list[ComponentInfo],
    values: dict[str, float],
) -> str:
    """Replace component values in the netlist with new values."""
    lines = netlist.splitlines()
    # Build lookup by line number for efficient single-pass replacement
    line_map: dict[int, ComponentInfo] = {c.line_num: c for c in components}

    result = []
    for i, line in enumerate(lines):
        if i in line_map:
            comp = line_map[i]
            new_val_str = format_engineering(values[comp.ref])
            if comp.source == "param":
                # Replace value in .param line
                new_line = re.sub(
                    r"(^\s*\.param\s+" + re.escape(comp.ref) + r"\s*=\s*)\S+",
                    r"\g<1>" + new_val_str,
                    line,
                    flags=re.IGNORECASE,
                )
                result.append(new_line)
            else:
                # Replace value in instance line: ref node1 node2 <value> [rest]
                new_line = re.sub(
                    r"(^\s*" + re.escape(comp.ref) + r"\s+\S+\s+\S+\s+)\S+",
                    r"\g<1>" + new_val_str,
                    line,
                    flags=re.IGNORECASE,
                )
                result.append(new_line)
        else:
            result.append(line)
    return "\n".join(result)


def generate_corners(n: int) -> list[tuple[int, ...]]:
    """Generate all 2^N corners for N components."""
    return list(itertools.product((-1, 1), repeat=n))


def compute_statistics(results_list: list[dict]) -> dict:
    """Compute statistics across a list of simulation result dicts.

    Flattens nested dicts (e.g. nodes) with dotted keys.
    Skips keys with only one unique value.
    """
    if not results_list:
        return {}

    # Collect all numeric values by key
    collected: dict[str, list[float]] = {}

    def _collect(d: dict, prefix: str = "") -> None:
        for key, val in d.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(val, dict):
                _collect(val, full_key)
            elif isinstance(val, (int, float)) and val is not None:
                # Skip booleans
                if isinstance(val, bool):
                    continue
                collected.setdefault(full_key, []).append(float(val))

    for r in results_list:
        _collect(r)

    # Compute stats, skipping keys with only one unique value
    stats: dict[str, dict] = {}
    for key, vals in collected.items():
        arr = np.array(vals)
        if len(set(vals)) <= 1:
            continue
        stats[key] = {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "median": float(np.median(arr)),
            "pct_5": float(np.percentile(arr, 5)),
            "pct_95": float(np.percentile(arr, 95)),
        }

    return stats


def build_analysis_cmd(analysis_type: str, **params) -> str:
    """Build a SPICE analysis command string from type and parameters."""
    if analysis_type == "ac":
        ppd = params.get("points_per_decade", 10)
        start = params.get("start_freq", 1)
        stop = params.get("stop_freq", 1e6)
        return f".ac dec {ppd} {start} {stop}"
    elif analysis_type == "transient":
        step = params["step_time"]
        stop = params["stop_time"]
        return f".tran {step} {stop}"
    elif analysis_type == "dc_op":
        return ".op"
    else:
        raise ValueError(f"Unknown analysis_type: {analysis_type}")


def run_single_sim(netlist: str, analysis_cmd: str) -> dict | None:
    """Run a single simulation with the given analysis command.

    Returns parsed results dict, or None on failure.
    """
    with tempfile.TemporaryDirectory(prefix="spicebridge_mc_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        # Strip old analysis/.end, append new ones
        lines = []
        for line in netlist.splitlines():
            if _ANALYSIS_RE.match(line):
                continue
            if _END_RE.match(line):
                continue
            lines.append(line)
        lines.append(analysis_cmd)
        lines.append(".end")
        prepared = "\n".join(lines) + "\n"

        try:
            success = run_simulation(prepared, output_dir=tmpdir_path)
            if not success:
                return None
            raw_path = tmpdir_path / "circuit.raw"
            return parse_results(raw_path)
        except Exception:
            return None


def compute_worst_case(
    nominal: dict,
    corner_results: list[tuple[tuple[int, ...], dict]],
    components: list[ComponentInfo],
    tolerances: dict | None,
    default_tol: float,
) -> dict:
    """Find min/max value + corner label for each numeric metric."""
    if not corner_results:
        return {}

    def _corner_label(corner: tuple[int, ...]) -> str:
        parts = []
        for comp, direction in zip(components, corner, strict=False):
            tol = _resolve_tolerance(comp.ref, tolerances, default_tol)
            sign = "+" if direction > 0 else "-"
            parts.append(f"{comp.ref}{sign}{tol}%")
        return ", ".join(parts)

    # Flatten nominal results
    def _flatten(d: dict, prefix: str = "") -> dict[str, float]:
        flat: dict[str, float] = {}
        for key, val in d.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(val, dict):
                flat.update(_flatten(val, full_key))
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                flat[full_key] = float(val)
        return flat

    nom_flat = _flatten(nominal)
    worst: dict[str, dict] = {}

    for key in nom_flat:
        best_min = nom_flat[key]
        best_max = nom_flat[key]
        min_corner = "nominal"
        max_corner = "nominal"

        for corner, result in corner_results:
            result_flat = _flatten(result)
            if key in result_flat:
                val = result_flat[key]
                if val < best_min:
                    best_min = val
                    min_corner = _corner_label(corner)
                if val > best_max:
                    best_max = val
                    max_corner = _corner_label(corner)

        # Only include metrics that actually vary
        if best_min != best_max:
            worst[key] = {
                "nominal": nom_flat[key],
                "min": best_min,
                "min_corner": min_corner,
                "max": best_max,
                "max_corner": max_corner,
            }

    return worst


def compute_sensitivity(
    nominal: dict,
    components: list[ComponentInfo],
    sensitivity_runs: list[tuple[str, int, dict]],
    tolerances: dict | None,
    default_tol: float,
) -> dict:
    """Compute per-component sensitivity.

    sensitivity_runs: list of (ref, direction, result_dict) tuples.
    Returns dict keyed by metric, containing sorted component sensitivities.
    """

    def _flatten(d: dict, prefix: str = "") -> dict[str, float]:
        flat: dict[str, float] = {}
        for key, val in d.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(val, dict):
                flat.update(_flatten(val, full_key))
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                flat[full_key] = float(val)
        return flat

    nom_flat = _flatten(nominal)

    # Collect per-component, per-direction results
    comp_results: dict[str, dict[int, dict[str, float]]] = {}
    for ref, direction, result in sensitivity_runs:
        comp_results.setdefault(ref, {})[direction] = _flatten(result)

    sensitivity: dict[str, list[dict]] = {}
    for metric_key, nom_val in nom_flat.items():
        if nom_val == 0:
            continue
        entries = []
        for comp in components:
            tol_pct = _resolve_tolerance(comp.ref, tolerances, default_tol)
            if comp.ref not in comp_results:
                continue
            dirs = comp_results[comp.ref]
            # Use whichever direction produces larger deviation
            max_delta_pct = 0.0
            for _direction, result_flat in dirs.items():
                if metric_key in result_flat:
                    delta_pct = (
                        (result_flat[metric_key] - nom_val) / abs(nom_val) * 100.0
                    )
                    if abs(delta_pct) > abs(max_delta_pct):
                        max_delta_pct = delta_pct

            pct_per_pct = max_delta_pct / tol_pct if tol_pct > 0 else 0.0

            entries.append(
                {
                    "component": comp.ref,
                    "pct_per_pct": round(pct_per_pct, 4),
                    "tolerance_pct": tol_pct,
                }
            )

        # Sort by absolute sensitivity descending
        entries.sort(key=lambda e: abs(e["pct_per_pct"]), reverse=True)
        # Only include metrics where at least one component has nonzero sensitivity
        if any(e["pct_per_pct"] != 0 for e in entries):
            sensitivity[metric_key] = entries

    return sensitivity
