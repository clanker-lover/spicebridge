"""Tests for new parser measurement functions — real simulations + parsing."""

import tempfile
from pathlib import Path

import pytest

from spicebridge.parser import read_ac_at_frequency, read_ac_bandwidth
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


def _simulate(netlist: str) -> Path:
    """Simulate and return raw file path."""
    tmpdir = tempfile.mkdtemp(prefix="spicebridge_test_")
    result = run_simulation(netlist, output_dir=tmpdir)
    assert result is True, "Simulation failed"
    return Path(tmpdir) / "circuit.raw"


def test_read_ac_at_frequency_near_dc():
    """At ~10Hz, gain should be ~0dB and phase ~0deg for RC lowpass."""
    raw_path = _simulate(AC_NETLIST)
    data = read_ac_at_frequency(raw_path, 10.0)
    assert abs(data["gain_db"]) < 1.0
    assert abs(data["phase_deg"]) < 10.0


def test_read_ac_at_frequency_at_f3db():
    """At f_3dB (~1592 Hz), gain should be ~-3dB."""
    raw_path = _simulate(AC_NETLIST)
    data = read_ac_at_frequency(raw_path, 1592.0)
    assert abs(data["gain_db"] - (-3.0)) < 1.0


def test_read_ac_at_frequency_out_of_range():
    """Frequency outside simulated range should raise ValueError."""
    raw_path = _simulate(AC_NETLIST)
    with pytest.raises(ValueError, match="outside simulated range"):
        read_ac_at_frequency(raw_path, 1e9)


def test_read_ac_bandwidth_3db():
    """3dB bandwidth should be ~1592 Hz for RC lowpass."""
    raw_path = _simulate(AC_NETLIST)
    bw = read_ac_bandwidth(raw_path, -3.0)
    assert bw["f_cutoff_hz"] is not None
    assert abs(bw["f_cutoff_hz"] - 1592) < 100


def test_read_ac_bandwidth_6db():
    """6dB cutoff should be higher than 3dB cutoff."""
    raw_path = _simulate(AC_NETLIST)
    bw_3 = read_ac_bandwidth(raw_path, -3.0)
    bw_6 = read_ac_bandwidth(raw_path, -6.0)
    assert bw_3["f_cutoff_hz"] is not None
    assert bw_6["f_cutoff_hz"] is not None
    assert bw_6["f_cutoff_hz"] > bw_3["f_cutoff_hz"]
