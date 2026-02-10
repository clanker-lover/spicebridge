"""End-to-end tests for spicebridge.server — call tool functions directly."""

from spicebridge.server import (
    create_circuit,
    get_results,
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


def test_full_ac_loop():
    """create_circuit -> run_ac_analysis -> get_results full loop."""
    result = create_circuit(RC_LOWPASS)
    assert result["status"] == "ok"
    cid = result["circuit_id"]
    assert len(cid) == 8

    ac = run_ac_analysis(cid, start_freq=1.0, stop_freq=1e6, points_per_decade=10)
    assert ac["status"] == "ok"
    assert "f_3dB_hz" in ac["results"]
    assert abs(ac["results"]["f_3dB_hz"] - 1592) < 100

    stored = get_results(cid)
    assert stored["status"] == "ok"
    assert stored["results"] == ac["results"]


def test_transient_loop():
    """create_circuit -> run_transient -> verify steady state."""
    result = create_circuit(RC_STEP)
    assert result["status"] == "ok"
    cid = result["circuit_id"]

    tran = run_transient(cid, stop_time=10e-3, step_time=10e-6)
    assert tran["status"] == "ok"
    assert abs(tran["results"]["steady_state_value"] - 5.0) < 0.1


def test_dc_op_loop():
    """create_circuit -> run_dc_op -> verify voltage divider."""
    result = create_circuit(VOLTAGE_DIVIDER)
    assert result["status"] == "ok"
    cid = result["circuit_id"]

    dc = run_dc_op(cid)
    assert dc["status"] == "ok"
    assert "v(out)" in dc["results"]["nodes"]
    assert abs(dc["results"]["nodes"]["v(out)"] - 5.0) < 0.01


def test_invalid_circuit_id_errors():
    """Operations on invalid circuit IDs should return error dicts."""
    for fn in [
        lambda: run_ac_analysis("deadbeef"),
        lambda: run_transient("deadbeef", stop_time=1e-3, step_time=1e-6),
        lambda: run_dc_op("deadbeef"),
        lambda: get_results("deadbeef"),
    ]:
        result = fn()
        assert result["status"] == "error"
        assert "not found" in result["error"]


def test_get_results_before_sim():
    """get_results before any simulation should return None results."""
    result = create_circuit(RC_LOWPASS)
    cid = result["circuit_id"]
    stored = get_results(cid)
    assert stored["status"] == "ok"
    assert stored["results"] is None
