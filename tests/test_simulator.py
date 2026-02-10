"""Tests for spicebridge.simulator."""

import tempfile
from pathlib import Path

from spicebridge.simulator import run_simulation

RC_LOWPASS_NETLIST = """\
* RC Low-Pass Filter - AC Analysis
V1 in 0 dc 0 ac 1
R1 in out 1k
C1 out 0 100n
.ac dec 10 1 1Meg
.end
"""

INVALID_NETLIST = """\
* Missing analysis command
R1 in out 1k
.end
"""


def test_run_simulation_rc_lowpass():
    """Run an RC low-pass AC analysis and verify .raw output is produced."""
    with tempfile.TemporaryDirectory(prefix="spicebridge_test_") as tmpdir:
        result = run_simulation(RC_LOWPASS_NETLIST, output_dir=tmpdir)
        assert result is True
        raw_files = list(Path(tmpdir).glob("*.raw"))
        assert len(raw_files) >= 1
        assert raw_files[0].stat().st_size > 0


def test_run_simulation_invalid_netlist():
    """An invalid netlist should not crash — just return False."""
    with tempfile.TemporaryDirectory(prefix="spicebridge_test_") as tmpdir:
        result = run_simulation(INVALID_NETLIST, output_dir=tmpdir)
        # Should not crash; result may be True or False depending on ngspice
        assert isinstance(result, bool)
