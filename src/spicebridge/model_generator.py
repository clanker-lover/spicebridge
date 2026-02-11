"""Generate SPICE model text from datasheet parameters.

Pure model-text generation — no file I/O.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


@dataclass
class GeneratedModel:
    """Container for a generated SPICE model."""

    name: str
    component_type: str
    spice_text: str
    parameters: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


_NAME_RE = re.compile(r"^[A-Za-z]\w*$")


def _validate_name(name: str) -> None:
    if not name:
        raise ValueError("Model name must not be empty")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid model name '{name}': must start with a letter "
            "and contain only alphanumeric characters and underscores"
        )


# ---------------------------------------------------------------------------
# Op-Amp behavioural subcircuit
# ---------------------------------------------------------------------------

_OPAMP_DEFAULTS: dict[str, float] = {
    "gbw_hz": 10e6,
    "dc_gain_db": 100,
    "slew_rate_v_us": 20,
    "input_offset_mv": 1,
    "input_bias_na": 10,
    "cmrr_db": 90,
    "psrr_db": 90,
    "output_impedance_ohm": 75,
    "supply_current_ma": 4,
    "vos_drift_uv_c": 5,
    "input_impedance_mohm": 1e6,
    "output_swing_v": 1.5,
    "supply_voltage_v": 15,
}


def _generate_opamp(name: str, params: dict) -> GeneratedModel:  # noqa: N802
    p = {**_OPAMP_DEFAULTS, **(params or {})}

    adc = 10 ** (p["dc_gain_db"] / 20)
    f_pole = p["gbw_hz"] / adc
    rpole = 1e6
    cpole = 1 / (2 * math.pi * f_pole * rpole)
    rin = p["input_impedance_mohm"] * 1e6
    ibias = p["input_bias_na"] * 1e-9
    vos = p["input_offset_mv"] * 1e-3
    cmrr_linear = 10 ** (p["cmrr_db"] / 20)
    islew = p["slew_rate_v_us"] * 1e6 * cpole
    rout = p["output_impedance_ohm"]
    swing = p["output_swing_v"]
    isupp = p["supply_current_ma"] * 1e-3

    # Build behavioural expression strings (must stay on one line each)
    egain_expr = (
        f"{adc:.6g}*((V(inp)-V(inn))-{vos:.6g}+(V(inp)+V(inn))/2/{cmrr_linear:.6g})"
    )
    gslew_expr = f"min(max((V(mid)-V(int))/{rpole:.6g},-{islew:.6g}),{islew:.6g})"
    bclamp_expr = f"min(max(V(int),V(vee)+{swing:.6g}),V(vcc)-{swing:.6g})"

    text = (
        f".subckt {name} inp inn out vcc vee\n"
        f"* Input stage\n"
        f"Rin inp inn {rin:.6g}\n"
        f"Ibp inp 0 DC {ibias:.6g}\n"
        f"Ibn inn 0 DC {ibias:.6g}\n"
        f"* Gain stage with CMRR error\n"
        f"Egain mid 0 VALUE={{{egain_expr}}}\n"
        f"* Dominant pole with slew rate limiting\n"
        f"Gslew 0 int VALUE={{{gslew_expr}}}\n"
        f"Cpole int 0 {cpole:.6g}\n"
        f"* Output stage: clamp then Rout\n"
        f"Bclamp clamped 0 V={{{bclamp_expr}}}\n"
        f"Rout clamped out {rout:.6g}\n"
        f"* Quiescent supply current\n"
        f"Isupp vcc vee DC {isupp:.6g}\n"
        f".ends {name}\n"
    )

    notes = []
    if p["psrr_db"] != _OPAMP_DEFAULTS["psrr_db"]:
        notes.append(
            f"PSRR ({p['psrr_db']} dB) stored in parameters "
            "but not modeled "
            "(requires supply-dependent gain modulation)."
        )
    else:
        notes.append(
            "PSRR not modeled in subcircuit "
            "(requires supply-dependent gain modulation)."
        )
    if p["vos_drift_uv_c"] != _OPAMP_DEFAULTS["vos_drift_uv_c"]:
        notes.append(
            f"Vos drift ({p['vos_drift_uv_c']} uV/C) stored "
            "in parameters but not modeled "
            "(requires .temp support)."
        )
    else:
        notes.append("Vos temperature drift not modeled (requires .temp support).")

    return GeneratedModel(
        name=name,
        component_type="opamp",
        spice_text=text,
        parameters=p,
        metadata={
            "calculated": {
                "Adc": adc,
                "f_pole": f_pole,
                "Rpole": rpole,
                "Cpole": cpole,
                "Islew": islew,
            },
        },
        notes=notes,
    )


# ---------------------------------------------------------------------------
# BJT — standard .model
# ---------------------------------------------------------------------------

_BJT_DEFAULTS: dict[str, object] = {
    "type": "NPN",
    "bf": 200,
    "is_a": 1e-14,
    "vaf_v": 100,
    "cje_pf": 5,
    "cjc_pf": 3,
    "tf_ns": 0.3,
    "rb_ohm": 10,
    "rc_ohm": 1,
    "re_ohm": 0.5,
}


def _generate_bjt(name: str, params: dict) -> GeneratedModel:
    p = {**_BJT_DEFAULTS, **(params or {})}
    bjt_type = str(p["type"]).upper()
    if bjt_type not in ("NPN", "PNP"):
        raise ValueError(f"BJT type must be NPN or PNP, got '{bjt_type}'")

    text = (
        f".model {name} {bjt_type} ("
        f"BF={p['bf']} "
        f"IS={float(p['is_a']):.4e} "
        f"VAF={p['vaf_v']} "
        f"CJE={p['cje_pf']}p "
        f"CJC={p['cjc_pf']}p "
        f"TF={p['tf_ns']}n "
        f"RB={p['rb_ohm']} "
        f"RC={p['rc_ohm']} "
        f"RE={p['re_ohm']}"
        f")\n"
    )

    return GeneratedModel(
        name=name,
        component_type="bjt",
        spice_text=text,
        parameters=p,
        metadata={},
        notes=[],
    )


# ---------------------------------------------------------------------------
# MOSFET — Level 1 .model
# ---------------------------------------------------------------------------

_MOSFET_DEFAULTS: dict[str, object] = {
    "type": "NMOS",
    "vth_v": 1.5,
    "kp_ua_v2": 200,
    "lambda_v": 0.02,
    "w_um": 10,
    "l_um": 1,
    "cbd_pf": 5,
    "cbs_pf": 5,
    "cgso_pf": 1,
    "cgdo_pf": 1,
}


def _generate_mosfet(name: str, params: dict) -> GeneratedModel:
    p = {**_MOSFET_DEFAULTS, **(params or {})}
    mos_type = str(p["type"]).upper()
    if mos_type not in ("NMOS", "PMOS"):
        raise ValueError(f"MOSFET type must be NMOS or PMOS, got '{mos_type}'")

    # PMOS default Vth should be negative if user didn't override
    vth = float(p["vth_v"])
    if mos_type == "PMOS" and "vth_v" not in (params or {}):
        vth = -abs(vth)

    text = (
        f".model {name} {mos_type} ("
        f"VTO={vth} "
        f"KP={p['kp_ua_v2']}u "
        f"LAMBDA={p['lambda_v']} "
        f"CBD={p['cbd_pf']}p "
        f"CBS={p['cbs_pf']}p "
        f"CGSO={p['cgso_pf']}p "
        f"CGDO={p['cgdo_pf']}p"
        f")\n"
    )

    return GeneratedModel(
        name=name,
        component_type="mosfet",
        spice_text=text,
        parameters=p,
        metadata={
            "instance_params": {
                "W": f"{p['w_um']}u",
                "L": f"{p['l_um']}u",
            },
        },
        notes=[],
    )


# ---------------------------------------------------------------------------
# Diode — standard .model
# ---------------------------------------------------------------------------

_DIODE_DEFAULTS: dict[str, object] = {
    "is_a": 1e-14,
    "n": 1.05,
    "bv_v": 100,
    "rs_ohm": 0.5,
    "cjo_pf": 5,
    "tt_ns": 5,
}


def _generate_diode(name: str, params: dict) -> GeneratedModel:
    p = {**_DIODE_DEFAULTS, **(params or {})}

    text = (
        f".model {name} D ("
        f"IS={float(p['is_a']):.4e} "
        f"N={p['n']} "
        f"BV={p['bv_v']} "
        f"RS={p['rs_ohm']} "
        f"CJO={p['cjo_pf']}p "
        f"TT={p['tt_ns']}n"
        f")\n"
    )

    return GeneratedModel(
        name=name,
        component_type="diode",
        spice_text=text,
        parameters=p,
        metadata={},
        notes=[],
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_GENERATORS = {
    "opamp": _generate_opamp,
    "bjt": _generate_bjt,
    "mosfet": _generate_mosfet,
    "diode": _generate_diode,
}


def list_component_types() -> list[str]:
    """Return supported component type strings."""
    return sorted(_GENERATORS.keys())


def get_default_parameters(component_type: str) -> dict:
    """Return the default parameter dict for *component_type*."""
    defaults_map = {
        "opamp": _OPAMP_DEFAULTS,
        "bjt": _BJT_DEFAULTS,
        "mosfet": _MOSFET_DEFAULTS,
        "diode": _DIODE_DEFAULTS,
    }
    if component_type not in defaults_map:
        raise ValueError(
            f"Unknown component type '{component_type}'. "
            f"Supported: {list_component_types()}"
        )
    return dict(defaults_map[component_type])


def generate_model(
    component_type: str,
    name: str,
    parameters: dict | None = None,
) -> GeneratedModel:
    """Generate a SPICE model from datasheet parameters.

    Parameters
    ----------
    component_type : str
        One of ``"opamp"``, ``"bjt"``, ``"mosfet"``, ``"diode"``.
    name : str
        Model name — must start with a letter, alphanumeric + underscore.
    parameters : dict, optional
        Datasheet values; omitted keys use sensible defaults.
    """
    _validate_name(name)
    if component_type not in _GENERATORS:
        raise ValueError(
            f"Unknown component type '{component_type}'. "
            f"Supported: {list_component_types()}"
        )
    return _GENERATORS[component_type](name, parameters or {})
