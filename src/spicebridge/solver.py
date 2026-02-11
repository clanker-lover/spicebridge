"""Design equation solvers for common circuit topologies."""

from __future__ import annotations

import math

from spicebridge.standard_values import format_engineering, snap_to_standard

# Practical component ranges
_R_MIN = 100
_R_MAX = 1e6
_C_MIN = 1e-12
_C_MAX = 100e-6


def _check_resistor(value: float, name: str, notes: list[str]) -> None:
    lo = format_engineering(_R_MIN)
    hi = format_engineering(_R_MAX)
    v = format_engineering(value)
    if value < _R_MIN:
        notes.append(f"{name} = {v} is below {lo} (excessive current)")
    elif value > _R_MAX:
        notes.append(f"{name} = {v} is above {hi} (noise-sensitive)")


def _check_capacitor(value: float, name: str, notes: list[str]) -> None:
    lo = format_engineering(_C_MIN)
    hi = format_engineering(_C_MAX)
    v = format_engineering(value)
    if value < _C_MIN:
        notes.append(f"{name} = {v} is below {lo} (parasitic)")
    elif value > _C_MAX:
        notes.append(f"{name} = {v} is above {hi} (large/expensive)")


def _pick_c_anchor(f_c: float) -> float:
    """Pick a capacitor value that puts R in a practical range for the given cutoff."""
    candidates = [10e-9, 1e-9, 100e-9, 100e-12, 1e-6]
    for c in candidates:
        r = 1.0 / (2.0 * math.pi * f_c * c)
        if _R_MIN <= r <= _R_MAX:
            return c
    return 10e-9  # fallback


def _build_nearest(components: dict[str, float], series: str = "E96") -> dict[str, str]:
    """Snap each component to the nearest standard value and format."""
    result: dict[str, str] = {"series": series}
    for name, value in components.items():
        snapped = snap_to_standard(value, series)
        result[name] = format_engineering(snapped)
    return result


# ---------------------------------------------------------------------------
# Topology solvers
# ---------------------------------------------------------------------------


def _solve_rc_lowpass_1st(specs: dict) -> dict:
    if "f_cutoff_hz" not in specs:
        raise ValueError("rc_lowpass_1st requires: f_cutoff_hz")
    f_c = specs["f_cutoff_hz"]
    if f_c <= 0:
        raise ValueError("f_cutoff_hz must be positive")

    notes: list[str] = []
    c1 = _pick_c_anchor(f_c)
    r1 = 1.0 / (2.0 * math.pi * f_c * c1)
    c1_fmt = format_engineering(c1)
    notes.append(f"C1 chosen as {c1_fmt}F, R1 calculated to match cutoff.")

    _check_resistor(r1, "R1", notes)
    _check_capacitor(c1, "C1", notes)

    raw = {"R1": r1, "C1": c1}
    return {
        "components": {"R1": format_engineering(r1), "C1": format_engineering(c1)},
        "equations_used": {"f_cutoff": "1 / (2 * pi * R1 * C1)"},
        "notes": notes,
        "nearest_standard": _build_nearest(raw),
    }


def _solve_rc_highpass_1st(specs: dict) -> dict:
    if "f_cutoff_hz" not in specs:
        raise ValueError("rc_highpass_1st requires: f_cutoff_hz")
    f_c = specs["f_cutoff_hz"]
    if f_c <= 0:
        raise ValueError("f_cutoff_hz must be positive")

    notes: list[str] = []
    c1 = _pick_c_anchor(f_c)
    r1 = 1.0 / (2.0 * math.pi * f_c * c1)
    c1_fmt = format_engineering(c1)
    notes.append(f"C1 chosen as {c1_fmt}F, R1 calculated to match cutoff.")

    _check_resistor(r1, "R1", notes)
    _check_capacitor(c1, "C1", notes)

    raw = {"R1": r1, "C1": c1}
    return {
        "components": {"R1": format_engineering(r1), "C1": format_engineering(c1)},
        "equations_used": {"f_cutoff": "1 / (2 * pi * R1 * C1)"},
        "notes": notes,
        "nearest_standard": _build_nearest(raw),
    }


def _solve_sallen_key_lowpass_2nd(specs: dict) -> dict:
    if "f_cutoff_hz" not in specs:
        raise ValueError("sallen_key_lowpass_2nd requires: f_cutoff_hz")
    f_c = specs["f_cutoff_hz"]
    if f_c <= 0:
        raise ValueError("f_cutoff_hz must be positive")
    q = specs.get("Q", 0.707)
    if q <= 0:
        raise ValueError("Q must be positive")

    notes: list[str] = []

    # Equal-R design: R1 = R2 = R, C1 = 4*Q^2 * C2
    c2 = _pick_c_anchor(f_c)
    c1 = 4.0 * q * q * c2
    # R = 1 / (2*pi*f_c*sqrt(C1*C2))
    r = 1.0 / (2.0 * math.pi * f_c * math.sqrt(c1 * c2))

    notes.append(f"Equal-R design with Q={q}.")
    c1_fmt = format_engineering(c1)
    c2_fmt = format_engineering(c2)
    notes.append(f"C2 chosen as {c2_fmt}F, C1 = 4*Q^2*C2 = {c1_fmt}F.")

    _check_resistor(r, "R1", notes)
    _check_capacitor(c1, "C1", notes)
    _check_capacitor(c2, "C2", notes)

    raw = {"R1": r, "R2": r, "C1": c1, "C2": c2}
    return {
        "components": {
            "R1": format_engineering(r),
            "R2": format_engineering(r),
            "C1": format_engineering(c1),
            "C2": format_engineering(c2),
        },
        "equations_used": {
            "f_cutoff": "1 / (2 * pi * sqrt(R1 * R2 * C1 * C2))",
            "Q": "sqrt(R1 * R2 * C1 * C2) / (C2 * (R1 + R2))",
        },
        "notes": notes,
        "nearest_standard": _build_nearest(raw),
    }


def _solve_inverting_opamp(specs: dict) -> dict:
    has_db = "gain_dB" in specs
    has_linear = "gain_linear" in specs
    if has_db and has_linear:
        raise ValueError("Specify exactly one of gain_dB or gain_linear, not both")
    if not has_db and not has_linear:
        raise ValueError("inverting_opamp requires: gain_dB or gain_linear")

    if has_db:
        gain_magnitude = 10 ** (abs(specs["gain_dB"]) / 20.0)
    else:
        gain_magnitude = abs(specs["gain_linear"])

    if gain_magnitude == 0:
        raise ValueError("Gain magnitude must be non-zero")

    input_impedance = specs.get("input_impedance_ohms", 10e3)
    if input_impedance <= 0:
        raise ValueError("input_impedance_ohms must be positive")

    notes: list[str] = []
    rin = input_impedance
    rf = gain_magnitude * rin

    _check_resistor(rin, "Rin", notes)
    _check_resistor(rf, "Rf", notes)

    raw = {"Rin": rin, "Rf": rf}
    return {
        "components": {"Rin": format_engineering(rin), "Rf": format_engineering(rf)},
        "equations_used": {"gain": "A_v = -Rf / Rin"},
        "notes": notes,
        "nearest_standard": _build_nearest(raw),
    }


def _solve_noninverting_opamp(specs: dict) -> dict:
    has_db = "gain_dB" in specs
    has_linear = "gain_linear" in specs
    if has_db and has_linear:
        raise ValueError("Specify exactly one of gain_dB or gain_linear, not both")
    if not has_db and not has_linear:
        raise ValueError("noninverting_opamp requires: gain_dB or gain_linear")

    gain = 10 ** (abs(specs["gain_dB"]) / 20.0) if has_db else abs(specs["gain_linear"])

    if gain < 1:
        raise ValueError(
            "Non-inverting gain must be >= 1 (use gain_linear=1 for unity buffer)"
        )

    notes: list[str] = []

    if gain == 1:
        notes.append("Unity-gain buffer: R1 open, R2 = 0 (short).")
        return {
            "components": {"R1": "open", "R2": "0"},
            "equations_used": {"gain": "A_v = 1 + R2 / R1"},
            "notes": notes,
            "nearest_standard": {},
        }

    r1 = 10e3
    r2 = (gain - 1) * r1

    _check_resistor(r1, "R1", notes)
    _check_resistor(r2, "R2", notes)
    notes.append("No built-in template exists for this topology.")

    raw = {"R1": r1, "R2": r2}
    return {
        "components": {"R1": format_engineering(r1), "R2": format_engineering(r2)},
        "equations_used": {"gain": "A_v = 1 + R2 / R1"},
        "notes": notes,
        "nearest_standard": _build_nearest(raw),
    }


def _solve_voltage_divider(specs: dict) -> dict:
    has_ratio = "ratio" in specs
    has_voltages = "output_voltage" in specs and "input_voltage" in specs

    if not has_ratio and not has_voltages:
        raise ValueError(
            "voltage_divider requires: ratio OR (output_voltage + input_voltage)"
        )

    if has_ratio:
        ratio = specs["ratio"]
    else:
        if specs["input_voltage"] == 0:
            raise ValueError("input_voltage must be non-zero")
        ratio = specs["output_voltage"] / specs["input_voltage"]

    if ratio <= 0 or ratio >= 1:
        raise ValueError(
            f"Voltage divider ratio must be between 0 and 1 (exclusive), got {ratio}"
        )

    notes: list[str] = []
    r2 = 10e3
    r1 = r2 * (1.0 - ratio) / ratio

    _check_resistor(r1, "R1", notes)
    _check_resistor(r2, "R2", notes)

    raw = {"R1": r1, "R2": r2}
    return {
        "components": {"R1": format_engineering(r1), "R2": format_engineering(r2)},
        "equations_used": {"ratio": "V_out / V_in = R2 / (R1 + R2)"},
        "notes": notes,
        "nearest_standard": _build_nearest(raw),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_SOLVERS = {
    "rc_lowpass_1st": _solve_rc_lowpass_1st,
    "rc_highpass_1st": _solve_rc_highpass_1st,
    "sallen_key_lowpass_2nd": _solve_sallen_key_lowpass_2nd,
    "inverting_opamp": _solve_inverting_opamp,
    "noninverting_opamp": _solve_noninverting_opamp,
    "voltage_divider": _solve_voltage_divider,
}


def solve(topology_id: str, specs: dict) -> dict:
    """Calculate component values for a circuit topology from target specs.

    Raises ValueError for unknown topology or invalid specs.
    """
    if topology_id not in _SOLVERS:
        available = sorted(_SOLVERS.keys())
        raise ValueError(f"Unknown topology '{topology_id}'. Available: {available}")
    return _SOLVERS[topology_id](specs)
