"""Tests for spicebridge.circuit_manager — unit tests, no simulations."""

import re

import pytest

from spicebridge.circuit_manager import CircuitManager


def test_create_returns_valid_id():
    """create() should return an 8-char hex string."""
    mgr = CircuitManager()
    cid = mgr.create("* test netlist\n.end\n")
    assert len(cid) == 8
    assert re.fullmatch(r"[0-9a-f]{8}", cid)


def test_create_output_dir_exists():
    """create() should create an existing output directory."""
    mgr = CircuitManager()
    cid = mgr.create("* test\n.end\n")
    state = mgr.get(cid)
    assert state.output_dir.is_dir()


def test_get_returns_same_state():
    """get() should return the same state that was created."""
    mgr = CircuitManager()
    netlist = "* my circuit\n.end\n"
    cid = mgr.create(netlist)
    state = mgr.get(cid)
    assert state.circuit_id == cid
    assert state.netlist == netlist
    assert state.last_results is None


def test_get_invalid_id_raises_keyerror():
    """get() should raise KeyError for an unknown circuit ID."""
    mgr = CircuitManager()
    with pytest.raises(KeyError, match="not found"):
        mgr.get("deadbeef")


def test_multiple_circuits_unique():
    """Multiple creates should produce unique IDs and directories."""
    mgr = CircuitManager()
    ids = [mgr.create(f"* circuit {i}\n.end\n") for i in range(5)]
    assert len(set(ids)) == 5
    dirs = [mgr.get(cid).output_dir for cid in ids]
    assert len(set(dirs)) == 5


def test_update_results():
    """update_results() should store results on the circuit state."""
    mgr = CircuitManager()
    cid = mgr.create("* test\n.end\n")
    results = {"analysis_type": "AC Analysis", "f_3dB_hz": 1592.0}
    mgr.update_results(cid, results)
    assert mgr.get(cid).last_results == results
