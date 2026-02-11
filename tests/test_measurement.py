"""Integration tests for measurement tools — real ngspice execution."""

from spicebridge.server import (
    compare_specs,
    create_circuit,
    measure_bandwidth,
    measure_dc,
    measure_gain,
    measure_power,
    measure_transient,
    run_ac_analysis,
    run_dc_op,
    run_transient,
)

RC_LOWPASS = """\
* RC Low-Pass Filter
V1 in 0 dc 0 ac 1
R1 in out 1k
C1 out 0 100n
"""

VOLTAGE_DIVIDER = """\
* Voltage Divider
V1 in 0 dc 10
R1 in out 1k
R2 out 0 1k
"""

RC_STEP = """\
* RC Step Response
V1 in 0 PULSE(0 5 0 1n 1n 100m 200m)
R1 in out 1k
C1 out 0 1u
"""


def _ac_circuit() -> str:
    """Create an RC lowpass circuit and run AC analysis, return circuit_id."""
    cid = create_circuit(RC_LOWPASS)["circuit_id"]
    run_ac_analysis(cid, start_freq=1.0, stop_freq=1e6, points_per_decade=10)
    return cid


def _dc_circuit() -> str:
    """Create a voltage divider circuit and run DC OP, return circuit_id."""
    cid = create_circuit(VOLTAGE_DIVIDER)["circuit_id"]
    run_dc_op(cid)
    return cid


def _tran_circuit() -> str:
    """Create an RC step circuit and run transient, return circuit_id."""
    cid = create_circuit(RC_STEP)["circuit_id"]
    run_transient(cid, stop_time=10e-3, step_time=10e-6)
    return cid


# --- measure_bandwidth ---


def test_measure_bandwidth_default_3db():
    cid = _ac_circuit()
    result = measure_bandwidth(cid)
    assert result["status"] == "ok"
    assert result["f_cutoff_hz"] is not None
    assert abs(result["f_cutoff_hz"] - 1592) < 100
    assert result["rolloff_db_per_decade"] is not None
    assert abs(result["rolloff_db_per_decade"] - (-20)) < 3
    assert result["threshold_db"] == -3.0


def test_measure_bandwidth_custom_6db():
    cid = _ac_circuit()
    result = measure_bandwidth(cid, threshold_db=-6.0)
    assert result["status"] == "ok"
    assert result["f_cutoff_hz"] is not None
    assert result["f_cutoff_hz"] > 1592  # 6dB cutoff is higher than 3dB


def test_measure_bandwidth_no_results():
    cid = create_circuit(RC_LOWPASS)["circuit_id"]
    result = measure_bandwidth(cid)
    assert result["status"] == "error"
    assert "No simulation results" in result["error"]


def test_measure_bandwidth_wrong_analysis():
    cid = _tran_circuit()
    result = measure_bandwidth(cid)
    assert result["status"] == "error"
    assert "Expected AC Analysis" in result["error"]


# --- measure_gain ---


def test_measure_gain_at_dc():
    cid = _ac_circuit()
    result = measure_gain(cid, frequency_hz=10.0)
    assert result["status"] == "ok"
    assert abs(result["gain_db"]) < 1.0


def test_measure_gain_at_f3db():
    cid = _ac_circuit()
    result = measure_gain(cid, frequency_hz=1592.0)
    assert result["status"] == "ok"
    assert abs(result["gain_db"] - (-3.0)) < 1.0


def test_measure_gain_out_of_range():
    cid = _ac_circuit()
    result = measure_gain(cid, frequency_hz=1e9)
    assert result["status"] == "error"
    assert "outside simulated range" in result["error"]


# --- measure_dc ---


def test_measure_dc_voltage_divider():
    cid = _dc_circuit()
    result = measure_dc(cid, node_name="v(out)")
    assert result["status"] == "ok"
    assert abs(result["voltage_V"] - 5.0) < 0.01


def test_measure_dc_node_not_found():
    cid = _dc_circuit()
    result = measure_dc(cid, node_name="v(nonexistent)")
    assert result["status"] == "error"
    assert "not found" in result["error"]
    assert "Available nodes" in result["error"]


def test_measure_dc_case_insensitive():
    cid = _dc_circuit()
    result = measure_dc(cid, node_name="V(OUT)")
    assert result["status"] == "ok"
    assert abs(result["voltage_V"] - 5.0) < 0.01


# --- measure_transient ---


def test_measure_transient_rc_step():
    cid = _tran_circuit()
    result = measure_transient(cid)
    assert result["status"] == "ok"
    assert abs(result["steady_state_V"] - 5.0) < 0.1
    assert result["rise_time_us"] is not None
    assert 1000 < result["rise_time_us"] < 5000  # ~2200us expected


# --- measure_power ---


def test_measure_power_voltage_divider():
    cid = _dc_circuit()
    result = measure_power(cid)
    assert result["status"] == "ok"
    # 10V / 2kΩ total = 5mA, power = 10V * 5mA = 50mW
    assert abs(result["total_power_mW"] - 50.0) < 5.0


# --- compare_specs ---


def test_compare_specs_all_pass():
    cid = _ac_circuit()
    result = compare_specs(
        cid,
        {
            "f_3dB_hz": {"target": 1592, "tolerance_pct": 10},
            "gain_dc_dB": {"min": -1.0, "max": 1.0},
        },
    )
    assert result["status"] == "ok"
    assert result["all_passed"] is True


def test_compare_specs_one_fails():
    cid = _ac_circuit()
    result = compare_specs(
        cid,
        {
            "f_3dB_hz": {"target": 5000, "tolerance_pct": 1},
        },
    )
    assert result["status"] == "ok"
    assert result["all_passed"] is False
    assert result["results"]["f_3dB_hz"]["passed"] is False


def test_compare_specs_dc_node():
    cid = _dc_circuit()
    result = compare_specs(
        cid,
        {
            "v(out)": {"target": 5.0, "tolerance_pct": 5},
        },
    )
    assert result["status"] == "ok"
    assert result["all_passed"] is True


def test_compare_specs_no_results():
    cid = create_circuit(RC_LOWPASS)["circuit_id"]
    result = compare_specs(cid, {"f_3dB_hz": {"target": 1592}})
    assert result["status"] == "error"
    assert "No simulation results" in result["error"]
