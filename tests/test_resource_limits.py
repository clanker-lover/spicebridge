"""Tests for resource limits and DoS prevention guardrails."""

from unittest.mock import patch

import pytest

from spicebridge.circuit_manager import _MAX_CIRCUITS, CircuitManager
from spicebridge.server import (
    _MAX_MONTE_CARLO_RUNS,
    _MAX_NETLIST_SIZE,
    _MAX_STAGES,
    connect_stages,
    create_circuit,
    delete_circuit,
    run_ac_analysis,
    run_monte_carlo,
    run_transient,
)

# ---------------------------------------------------------------------------
# Monte Carlo limits
# ---------------------------------------------------------------------------


def test_monte_carlo_rejects_101_runs():
    """run_monte_carlo should reject num_runs > _MAX_MONTE_CARLO_RUNS."""
    result = create_circuit("* test\nR1 in out 1k\nC1 out 0 100n\n")
    assert result["status"] == "ok"
    cid = result["circuit_id"]

    mc = run_monte_carlo(cid, analysis_type="ac", num_runs=_MAX_MONTE_CARLO_RUNS + 1)
    assert mc["status"] == "error"
    assert "num_runs" in mc["error"]


def test_monte_carlo_accepts_max_runs():
    """run_monte_carlo should accept num_runs == _MAX_MONTE_CARLO_RUNS."""
    result = create_circuit("* test\nR1 in out 1k\nC1 out 0 100n\n")
    assert result["status"] == "ok"
    cid = result["circuit_id"]

    # Mock run_single_sim to avoid actual simulation
    with patch("spicebridge.server.run_single_sim", return_value={"gain_db": -3.0}):
        mc = run_monte_carlo(
            cid,
            analysis_type="ac",
            num_runs=_MAX_MONTE_CARLO_RUNS,
            start_freq=1,
            stop_freq=1e6,
        )
    assert mc["status"] == "ok"


# ---------------------------------------------------------------------------
# Input size limits
# ---------------------------------------------------------------------------


def test_create_circuit_rejects_large_netlist():
    """create_circuit should reject netlists exceeding _MAX_NETLIST_SIZE."""
    big_netlist = "x" * (_MAX_NETLIST_SIZE + 1)
    result = create_circuit(big_netlist)
    assert result["status"] == "error"
    assert "too large" in result["error"].lower()


def test_connect_stages_rejects_too_many():
    """connect_stages should reject more than _MAX_STAGES stages."""
    fake_stages = [{"circuit_id": "fake"}] * (_MAX_STAGES + 1)
    result = connect_stages(fake_stages)
    assert result["status"] == "error"
    assert "too many stages" in result["error"].lower()


# ---------------------------------------------------------------------------
# AC analysis parameter validation
# ---------------------------------------------------------------------------


def test_ac_rejects_high_points_per_decade():
    """run_ac_analysis should reject points_per_decade > 1000."""
    result = create_circuit("* test\nR1 in out 1k\nC1 out 0 100n\n")
    cid = result["circuit_id"]
    ac = run_ac_analysis(cid, points_per_decade=1001)
    assert ac["status"] == "error"
    assert "points_per_decade" in ac["error"]


def test_ac_rejects_bad_freq_range():
    """run_ac_analysis should reject start_freq > stop_freq."""
    result = create_circuit("* test\nR1 in out 1k\nC1 out 0 100n\n")
    cid = result["circuit_id"]
    ac = run_ac_analysis(cid, start_freq=1e6, stop_freq=1.0)
    assert ac["status"] == "error"
    assert "stop_freq" in ac["error"]


# ---------------------------------------------------------------------------
# Transient analysis parameter validation
# ---------------------------------------------------------------------------


def test_transient_rejects_too_many_steps():
    """run_transient should reject stop_time/step_time > 1M."""
    result = create_circuit("* test\nR1 in out 1k\nC1 out 0 100n\n")
    cid = result["circuit_id"]
    tran = run_transient(cid, stop_time=10.0, step_time=1e-8)
    assert tran["status"] == "error"
    assert "1,000,000" in tran["error"]


# ---------------------------------------------------------------------------
# Circuit manager eviction
# ---------------------------------------------------------------------------


def test_circuit_manager_eviction():
    """Creating _MAX_CIRCUITS + 1 circuits should evict the oldest."""
    mgr = CircuitManager()
    ids = []
    for i in range(_MAX_CIRCUITS + 1):
        cid = mgr.create(f"* circuit {i}\n.end\n")
        ids.append(cid)

    # Should have at most _MAX_CIRCUITS circuits
    assert len(mgr.list_all()) <= _MAX_CIRCUITS

    # First circuit should have been evicted
    with pytest.raises(KeyError):
        mgr.get(ids[0])

    # Last circuit should still exist
    mgr.get(ids[-1])


# ---------------------------------------------------------------------------
# delete_circuit
# ---------------------------------------------------------------------------


def test_delete_circuit_removes():
    """Deleting a circuit should make it unavailable."""
    mgr = CircuitManager()
    cid = mgr.create("* test\n.end\n")
    mgr.delete(cid)
    with pytest.raises(KeyError):
        mgr.get(cid)


def test_delete_circuit_error_for_missing():
    """delete_circuit MCP tool should return error for nonexistent circuit."""
    result = delete_circuit("nonexistent_id")
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


def test_delete_circuit_cleans_dir():
    """Deleting a circuit should remove its output directory."""
    mgr = CircuitManager()
    cid = mgr.create("* test\n.end\n")
    output_dir = mgr.get(cid).output_dir
    assert output_dir.is_dir()

    mgr.delete(cid)
    assert not output_dir.exists()


# ---------------------------------------------------------------------------
# cleanup_all
# ---------------------------------------------------------------------------


def test_cleanup_all():
    """cleanup_all should remove all circuits and their directories."""
    mgr = CircuitManager()
    dirs = []
    for i in range(5):
        cid = mgr.create(f"* circuit {i}\n.end\n")
        dirs.append(mgr.get(cid).output_dir)

    for d in dirs:
        assert d.is_dir()

    mgr.cleanup_all()

    assert len(mgr.list_all()) == 0
    for d in dirs:
        assert not d.exists()
