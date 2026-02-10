"""Tests for spicebridge.parser — real simulations + parsing."""

import tempfile
from pathlib import Path

from spicebridge.parser import (
    detect_analysis_type,
    parse_ac,
    parse_dc_op,
    parse_results,
    parse_transient,
)
from spicebridge.simulator import run_simulation

# RC lowpass: R=1k, C=100n -> f_3dB = 1/(2*pi*R*C) ≈ 1592 Hz
AC_NETLIST = """\
* RC Low-Pass Filter - AC Analysis
V1 in 0 dc 0 ac 1
R1 in out 1k
C1 out 0 100n
.ac dec 10 1 1Meg
.end
"""

# Voltage divider: 10V, 2x1k -> v(out) ≈ 5V
DC_OP_NETLIST = """\
* Voltage Divider - DC Operating Point
V1 in 0 dc 10
R1 in out 1k
R2 out 0 1k
.op
.end
"""

# RC step response: R=1k, C=1u -> tau=1ms, sim=10ms (>>tau for settling)
TRAN_NETLIST = """\
* RC Step Response - Transient
V1 in 0 PULSE(0 5 0 1n 1n 100m 200m)
R1 in out 1k
C1 out 0 1u
.tran 10u 10m
.end
"""


def _simulate(netlist: str) -> Path:
    """Simulate and return raw file path."""
    tmpdir = tempfile.mkdtemp(prefix="spicebridge_test_")
    result = run_simulation(netlist, output_dir=tmpdir)
    assert result is True, "Simulation failed"
    return Path(tmpdir) / "circuit.raw"


def test_ac_parse_f3db():
    """AC analysis: f_3dB should be approximately 1592 Hz for R=1k, C=100n."""
    raw_path = _simulate(AC_NETLIST)
    result = parse_ac(raw_path)

    assert result["analysis_type"] == "AC Analysis"
    assert result["f_3dB_hz"] is not None
    # f_3dB = 1/(2*pi*1000*100e-9) ≈ 1591.5 Hz, allow 5% tolerance
    assert abs(result["f_3dB_hz"] - 1592) < 100
    # DC gain should be ~0 dB (unity gain at DC for passive filter)
    assert abs(result["gain_dc_dB"]) < 0.5
    # Rolloff should be approximately -20 dB/decade for single-pole
    assert result["rolloff_rate_dB_per_decade"] is not None
    assert abs(result["rolloff_rate_dB_per_decade"] - (-20)) < 3
    assert result["num_points"] > 0
    assert len(result["freq_range"]) == 2


def test_dc_op_voltage_divider():
    """DC OP: voltage divider 10V/2x1k should give v(out) ≈ 5V."""
    raw_path = _simulate(DC_OP_NETLIST)
    result = parse_dc_op(raw_path)

    assert result["analysis_type"] == "Operating Point"
    assert "v(out)" in result["nodes"]
    assert abs(result["nodes"]["v(out)"] - 5.0) < 0.01
    assert result["num_nodes"] >= 2


def test_transient_rc_step():
    """Transient: RC step response should settle to ~5V."""
    raw_path = _simulate(TRAN_NETLIST)
    result = parse_transient(raw_path)

    assert result["analysis_type"] == "Transient Analysis"
    # Steady state should be close to 5V (RC charges to Vdc)
    assert abs(result["steady_state_value"] - 5.0) < 0.1
    # Rise time should be reasonable (tau=1ms, 10-90% ≈ 2.2*tau ≈ 2.2ms)
    assert result["rise_time_10_90_s"] is not None
    assert 0.001 < result["rise_time_10_90_s"] < 0.005
    assert result["num_points"] > 0


def test_detect_analysis_type_ac():
    """detect_analysis_type should return 'AC Analysis' for AC sim."""
    raw_path = _simulate(AC_NETLIST)
    assert "AC" in detect_analysis_type(raw_path)


def test_detect_analysis_type_dc_op():
    """detect_analysis_type should return 'Operating Point' for DC OP sim."""
    raw_path = _simulate(DC_OP_NETLIST)
    assert "Operating Point" in detect_analysis_type(raw_path)


def test_detect_analysis_type_transient():
    """detect_analysis_type should return 'Transient Analysis' for tran sim."""
    raw_path = _simulate(TRAN_NETLIST)
    assert "Transient" in detect_analysis_type(raw_path)


def test_parse_results_dispatches_ac():
    """parse_results should dispatch to parse_ac for AC sims."""
    raw_path = _simulate(AC_NETLIST)
    result = parse_results(raw_path)
    assert result["analysis_type"] == "AC Analysis"
    assert "f_3dB_hz" in result


def test_parse_results_dispatches_dc_op():
    """parse_results should dispatch to parse_dc_op for DC OP sims."""
    raw_path = _simulate(DC_OP_NETLIST)
    result = parse_results(raw_path)
    assert result["analysis_type"] == "Operating Point"
    assert "nodes" in result


def test_parse_results_dispatches_transient():
    """parse_results should dispatch to parse_transient for transient sims."""
    raw_path = _simulate(TRAN_NETLIST)
    result = parse_results(raw_path)
    assert result["analysis_type"] == "Transient Analysis"
    assert "steady_state_value" in result
