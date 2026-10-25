"""Behavioural checks of the built-in circuit templates (needs ngspice)."""

import json
import math
import re
import shutil

import pytest

from spicebridge.parser import parse_transient
from spicebridge.server import (
    _manager,
    load_template,
    measure_gain,
    run_ac_analysis,
)
from spicebridge.simulator import run_simulation
from spicebridge.standard_values import parse_spice_value
from spicebridge.template_manager import TemplateManager

needs_ngspice = pytest.mark.skipif(
    shutil.which("ngspice") is None, reason="ngspice not installed"
)

SALLEN_KEY = ["sallen_key_lowpass_2nd", "sallen_key_hpf_2nd"]

# Op-amp with a single pole (A0 = 1e5, GBW = 1 MHz) and the same ports as
# the templates' ideal_opamp. The ideal VCVS has no dynamics, so it hides
# positive feedback: out = A*(out - n2) has the same solution as a follower.
# With a pole, positive feedback makes the output run away.
_SINGLE_POLE_OPAMP = """\
.subckt ideal_opamp inp inn out
G1 0 x inp inn 1m
Rp x 0 100meg
Cp x 0 159p
E1 out 0 x 0 1
.ends ideal_opamp"""


def _builtin_netlist(template_id: str) -> str:
    path = TemplateManager._builtin_dir() / f"{template_id}.json"
    return json.loads(path.read_text())["netlist"]


def _builtin_templates() -> list[str]:
    return sorted(p.stem for p in TemplateManager._builtin_dir().glob("*.json"))


@pytest.mark.parametrize("template_id", _builtin_templates())
def test_opamps_are_not_wired_for_positive_feedback(template_id):
    """No op-amp instance may drive its own non-inverting input."""
    netlist = _builtin_netlist(template_id)
    for line in netlist.splitlines():
        tokens = line.split()
        if tokens and tokens[0][0].upper() == "X" and tokens[-1] == "ideal_opamp":
            inp, _inn, out = tokens[1:4]
            assert inp != out, f"{template_id}: {line}"


@needs_ngspice
@pytest.mark.parametrize("template_id", SALLEN_KEY)
def test_sallen_key_step_response_is_stable(template_id, tmp_path):
    netlist = re.sub(
        r"\.subckt ideal_opamp.*?\.ends ideal_opamp",
        _SINGLE_POLE_OPAMP,
        _builtin_netlist(template_id),
        flags=re.S,
    )
    netlist = netlist.replace(
        "V1 in 0 dc 0 ac 1", "V1 in 0 PULSE(0 1 1m 1u 1u 50m 100m)"
    )
    netlist += "\n.tran 1u 20m\n.end\n"
    assert run_simulation(netlist, output_dir=tmp_path)
    result = parse_transient(tmp_path / "circuit.raw")
    assert "error" not in result, result
    # Butterworth overshoots by about 4 %; a runaway output would be huge
    assert abs(result["peak_value"]) < 1.5
    expected_final = 1.0 if "lowpass" in template_id else 0.0
    assert result["steady_state_value"] == pytest.approx(expected_final, abs=0.02)


def _corner_frequency(circuit_id: str) -> float:
    vals = {}
    for line in _manager.get(circuit_id).netlist.splitlines():
        if line.lower().startswith(".param"):
            name, value = line.split()[1].split("=")
            vals[name] = parse_spice_value(value)
    product = vals["R1"] * vals["R2"] * vals["C1"] * vals["C2"]
    return 1.0 / (2 * math.pi * math.sqrt(product))


@needs_ngspice
@pytest.mark.parametrize("template_id", SALLEN_KEY)
@pytest.mark.parametrize("specs", [None, {"f_cutoff_hz": 1000}])
def test_sallen_key_is_butterworth(template_id, specs):
    """Default values and solver values both give Q = 0.707."""
    res = load_template(template_id, specs=specs)
    assert res["status"] == "ok"
    cid = res["circuit_id"]
    f0 = _corner_frequency(cid)
    assert run_ac_analysis(cid, 1, 1e6, 100)["status"] == "ok"

    def gain_db(f):
        return measure_gain(cid, f)["gain_db"]

    # A 2nd-order section has |H(f0)| = Q
    assert 10 ** (gain_db(f0) / 20) == pytest.approx(0.707, abs=0.02)
    passband, stopband = (f0 / 100, f0 * 10)
    if "hpf" in template_id:
        passband, stopband = stopband * 10, f0 / 10
    assert gain_db(passband) == pytest.approx(0.0, abs=0.1)
    assert gain_db(stopband) == pytest.approx(-40.0, abs=1.0)
