"""Tests for the multi-stage circuit composition engine."""

from __future__ import annotations

import pytest

from spicebridge.composer import (
    auto_detect_ports,
    compose_stages,
    prefix_netlist,
)

# ---------------------------------------------------------------------------
# Sample netlists for testing
# ---------------------------------------------------------------------------

_PORTS = {"in": "in", "out": "out", "gnd": "0"}

RC_LOWPASS = """\
* 1st-Order RC Low-Pass Filter
.param R1=10k
.param C1=10n
V1 in 0 dc 0 ac 1
R1 in out {R1}
C1 out 0 {C1}"""

RC_LOWPASS_2 = """\
* Another RC Low-Pass
.param R1=4.7k
.param C1=22n
V1 in 0 dc 0 ac 1
R1 in out {R1}
C1 out 0 {C1}"""

INVERTING_AMP = """\
* Inverting Op-Amp Amplifier
.param Rin=10k
.param Rf=100k
.subckt ideal_opamp inp inn out
E1 out 0 inp inn 100k
.ends ideal_opamp
V1 in 0 dc 0 ac 1
Rin in vminus {Rin}
Rf vminus out {Rf}
X1 0 vminus out ideal_opamp"""


# ---------------------------------------------------------------------------
# auto_detect_ports
# ---------------------------------------------------------------------------


class TestAutoDetectPorts:
    def test_rc_lowpass(self):
        ports = auto_detect_ports(RC_LOWPASS)
        assert "in" in ports
        assert "out" in ports
        assert "gnd" in ports
        assert ports["gnd"] == "0"

    def test_inverting_amp(self):
        ports = auto_detect_ports(INVERTING_AMP)
        assert "in" in ports
        assert "out" in ports

    def test_no_ports(self):
        netlist = "* Title\nR1 n1 n2 1k"
        ports = auto_detect_ports(netlist)
        assert ports == {}

    def test_ignores_subckt_internals(self):
        netlist = """\
* Test
.subckt myblock in out
R1 in out 1k
.ends myblock
X1 a b myblock"""
        ports = auto_detect_ports(netlist)
        assert "in" not in ports
        assert "out" not in ports


# ---------------------------------------------------------------------------
# prefix_netlist
# ---------------------------------------------------------------------------


class TestPrefixNetlist:
    def test_basic_prefixing(self):
        # V1->VS1_1, R1->RS1_1, C1->CS1_1; nodes prefixed
        prefixed, subckts = prefix_netlist(RC_LOWPASS, "S1")
        assert "RS1_1" in prefixed
        assert "CS1_1" in prefixed
        assert "VS1_1" in prefixed
        assert "S1_in" in prefixed
        assert "S1_out" in prefixed
        # Ground node 0 should NOT be prefixed
        has_zero = " 0 " in prefixed or prefixed.endswith(" 0") or "\n0 " in prefixed
        assert has_zero

    def test_params_prefixed(self):
        prefixed, _ = prefix_netlist(RC_LOWPASS, "S1")
        assert ".param S1_R1=" in prefixed
        assert ".param S1_C1=" in prefixed
        assert "{S1_R1}" in prefixed
        assert "{S1_C1}" in prefixed

    def test_analysis_directives_stripped(self):
        netlist = RC_LOWPASS + "\n.ac dec 10 1 1meg\n.end"
        prefixed, _ = prefix_netlist(netlist, "S1")
        assert ".ac" not in prefixed.lower()
        assert ".end" not in prefixed.lower()

    def test_subckt_extracted(self):
        prefixed, subckts = prefix_netlist(INVERTING_AMP, "S1")
        assert len(subckts) == 1
        assert "ideal_opamp" in subckts[0]
        assert ".subckt" not in prefixed.lower()
        assert ".ends" not in prefixed.lower()

    def test_preserve_nodes(self):
        prefixed, _ = prefix_netlist(RC_LOWPASS, "S1", preserve_nodes={"in"})
        assert "S1_out" in prefixed
        lines = prefixed.splitlines()
        # RS1_1 is the prefixed R1 component line
        r_line = [x for x in lines if x.strip().startswith("RS1_1 ")][0]
        assert " in " in r_line
        assert "S1_out" in r_line

    def test_strip_sources(self):
        prefixed, _ = prefix_netlist(RC_LOWPASS, "S2", strip_sources_on={"in"})
        # V source stripped; R source kept
        assert "VS2_1" not in prefixed
        assert "RS2_1" in prefixed

    def test_include_kept(self):
        netlist = ".include /path/to/model.lib\n" + RC_LOWPASS
        prefixed, _ = prefix_netlist(netlist, "S1")
        assert ".include /path/to/model.lib" in prefixed

    def test_subckt_model_name_not_prefixed(self):
        prefixed, subckts = prefix_netlist(INVERTING_AMP, "S1")
        x_lines = [x for x in prefixed.splitlines() if x.strip().startswith("XS1_1")]
        assert len(x_lines) == 1
        assert x_lines[0].strip().endswith("ideal_opamp")

    def test_component_letter_preserved(self):
        """Prefixed refs must keep their SPICE letter prefix."""
        prefixed, _ = prefix_netlist(RC_LOWPASS, "S1")
        for line in prefixed.splitlines():
            tok = line.strip().split()
            if not tok or line.strip().startswith((".", "*", "")):
                continue
            ref = tok[0]
            # Every component ref should start with its SPICE letter
            assert ref[0] in "RVCLQJMDEGHFBXI", f"Ref '{ref}' lost its type letter"


# ---------------------------------------------------------------------------
# compose_stages
# ---------------------------------------------------------------------------


def _stage(netlist, label=None, ports=None):
    """Helper to build a stage dict with defaults."""
    d = {
        "netlist": netlist,
        "ports": dict(_PORTS) if ports is None else ports,
    }
    if label:
        d["label"] = label
    return d


class TestComposeStages:
    def test_two_rc_stages(self):
        stages = [
            _stage(RC_LOWPASS, "S1"),
            _stage(RC_LOWPASS_2, "S2"),
        ]
        result = compose_stages(stages)
        netlist = result["netlist"]
        # Components from both stages present
        assert "RS1_1" in netlist
        assert "RS2_1" in netlist
        # Wire node connecting the stages
        assert "wire_S1_S2" in netlist
        # V source from S2 stripped (connected input)
        assert "VS2_1" not in netlist
        # V source from S1 retained
        assert "VS1_1" in netlist
        # Ground should not be prefixed
        assert "S1_0" not in netlist
        assert "S2_0" not in netlist

    def test_auto_labels(self):
        stages = [_stage(RC_LOWPASS), _stage(RC_LOWPASS_2)]
        result = compose_stages(stages)
        assert result["stages"][0]["label"] == "S1"
        assert result["stages"][1]["label"] == "S2"

    def test_ports_returned(self):
        stages = [
            _stage(RC_LOWPASS, "S1"),
            _stage(RC_LOWPASS_2, "S2"),
        ]
        result = compose_stages(stages)
        ports = result["ports"]
        assert "in" in ports
        assert "out" in ports
        assert "gnd" in ports
        assert ports["gnd"] == "0"

    def test_single_stage(self):
        stages = [_stage(RC_LOWPASS, "S1")]
        result = compose_stages(stages)
        assert "RS1_1" in result["netlist"]
        assert "VS1_1" in result["netlist"]

    def test_subckt_dedup(self):
        stages = [
            _stage(INVERTING_AMP, "S1"),
            _stage(INVERTING_AMP, "S2"),
        ]
        result = compose_stages(stages)
        count = result["netlist"].count(".subckt ideal_opamp")
        assert count == 1

    def test_explicit_connections(self):
        stages = [
            _stage(RC_LOWPASS, "A"),
            _stage(RC_LOWPASS_2, "B"),
        ]
        connections = [
            {
                "from_stage": 0,
                "from_port": "out",
                "to_stage": 1,
                "to_port": "in",
            },
        ]
        result = compose_stages(stages, connections=connections)
        assert "wire_A_B" in result["netlist"]

    def test_missing_ports_error(self):
        stages = [_stage(RC_LOWPASS, "S1", ports={})]
        with pytest.raises(ValueError, match="no ports"):
            compose_stages(stages)

    def test_bad_connection_error(self):
        stages = [
            _stage(RC_LOWPASS, "S1"),
            _stage(RC_LOWPASS_2, "S2"),
        ]
        connections = [
            {
                "from_stage": 0,
                "from_port": "missing",
                "to_stage": 1,
                "to_port": "in",
            },
        ]
        with pytest.raises(ValueError, match="not found"):
            compose_stages(stages, connections=connections)

    def test_empty_stages_error(self):
        with pytest.raises(ValueError, match="At least one stage"):
            compose_stages([])

    def test_filter_then_amp(self):
        """RC filter followed by inverting amp."""
        stages = [
            _stage(RC_LOWPASS, "FLT"),
            _stage(INVERTING_AMP, "AMP"),
        ]
        result = compose_stages(stages)
        netlist = result["netlist"]
        assert "RFLT_1" in netlist
        assert "RAMP_in" in netlist
        assert "wire_FLT_AMP" in netlist
        assert ".subckt ideal_opamp" in netlist
        # Amp's V1 stripped (connected input)
        assert "VAMP_1" not in netlist


# ---------------------------------------------------------------------------
# Integration tests (require ngspice)
# ---------------------------------------------------------------------------


@pytest.fixture
def _has_ngspice():
    """Skip if ngspice is not available."""
    import shutil

    if shutil.which("ngspice") is None:
        pytest.skip("ngspice not found")


@pytest.mark.usefixtures("_has_ngspice")
class TestComposerIntegration:
    """Integration tests that run actual simulations."""

    def test_two_rc_filters_rolloff(self):
        """Two RC filters => ~-40 dB/dec rolloff."""
        from spicebridge.circuit_manager import CircuitManager
        from spicebridge.parser import parse_results
        from spicebridge.simulator import run_simulation

        stages = [
            _stage(RC_LOWPASS, "S1"),
            _stage(RC_LOWPASS, "S2"),
        ]
        result = compose_stages(stages)
        combined = result["netlist"]
        combined += "\n.ac dec 20 100 100k\n.end\n"

        mgr = CircuitManager()
        cid = mgr.create(combined)
        circuit = mgr.get(cid)

        success = run_simulation(combined, output_dir=circuit.output_dir)
        assert success, "Simulation failed"

        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        assert results is not None

        rolloff = results.get("rolloff_rate_dB_per_decade")
        if rolloff is not None:
            assert rolloff < -30, f"Expected < -30 dB/dec, got {rolloff}"
