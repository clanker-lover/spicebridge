"""Tests for spicebridge.schematic — parser and renderer."""

import os
import tempfile
from pathlib import Path

import pytest

from spicebridge.schematic import ParsedComponent, draw_schematic, parse_netlist
from spicebridge.server import create_circuit
from spicebridge.server import draw_schematic as server_draw_schematic

# --- Test netlists ---

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

BJT_AMPLIFIER = """\
* Simple BJT Amplifier
V1 vcc 0 dc 12
R1 vcc base 100k
R2 base 0 10k
Q1 vcc base emitter 2N2222
R3 emitter 0 1k
"""

EMPTY_NETLIST = """\
* Just a comment
.title Empty
"""


# ==================== Parser unit tests ====================


class TestParseNetlist:
    def test_rc_lowpass_component_count(self):
        comps = parse_netlist(RC_LOWPASS)
        assert len(comps) == 3

    def test_rc_lowpass_component_types(self):
        comps = parse_netlist(RC_LOWPASS)
        types = [c.comp_type for c in comps]
        assert types == ["V", "R", "C"]

    def test_rc_lowpass_refs(self):
        comps = parse_netlist(RC_LOWPASS)
        refs = [c.ref for c in comps]
        assert refs == ["V1", "R1", "C1"]

    def test_rc_lowpass_nodes(self):
        comps = parse_netlist(RC_LOWPASS)
        # V1: in, 0
        assert comps[0].nodes == ["in", "0"]
        # R1: in, out
        assert comps[1].nodes == ["in", "out"]
        # C1: out, 0
        assert comps[2].nodes == ["out", "0"]

    def test_rc_lowpass_values(self):
        comps = parse_netlist(RC_LOWPASS)
        assert comps[0].value == "dc 0 ac 1"
        assert comps[1].value == "1k"
        assert comps[2].value == "100n"

    def test_bjt_amplifier_q_has_three_nodes(self):
        comps = parse_netlist(BJT_AMPLIFIER)
        q_comps = [c for c in comps if c.comp_type == "Q"]
        assert len(q_comps) == 1
        assert len(q_comps[0].nodes) == 3
        assert q_comps[0].nodes == ["vcc", "base", "emitter"]

    def test_bjt_amplifier_component_count(self):
        comps = parse_netlist(BJT_AMPLIFIER)
        assert len(comps) == 5

    def test_comments_and_directives_skipped(self):
        netlist = """\
* Comment
.title Test
V1 in 0 dc 1
.ac dec 10 1 1e6
R1 in out 1k
.end
"""
        comps = parse_netlist(netlist)
        assert len(comps) == 2
        assert comps[0].ref == "V1"
        assert comps[1].ref == "R1"

    def test_empty_netlist_returns_empty_list(self):
        comps = parse_netlist(EMPTY_NETLIST)
        assert comps == []

    def test_parsed_component_type(self):
        comps = parse_netlist(RC_LOWPASS)
        for comp in comps:
            assert isinstance(comp, ParsedComponent)

    def test_voltage_divider_parse(self):
        comps = parse_netlist(VOLTAGE_DIVIDER)
        assert len(comps) == 3
        r_comps = [c for c in comps if c.comp_type == "R"]
        assert len(r_comps) == 2
        assert r_comps[0].value == "1k"
        assert r_comps[1].value == "1k"


# ==================== Renderer unit tests ====================


class TestDrawSchematic:
    def test_png_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "schematic.png"
            result = draw_schematic(RC_LOWPASS, out, fmt="png")
            assert result == out
            assert out.exists()
            assert os.path.getsize(out) > 0

    def test_svg_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "schematic.svg"
            result = draw_schematic(RC_LOWPASS, out, fmt="svg")
            assert result == out
            assert out.exists()
            assert os.path.getsize(out) > 0

    def test_voltage_divider_schematic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "divider.png"
            result = draw_schematic(VOLTAGE_DIVIDER, out, fmt="png")
            assert result == out
            assert out.exists()
            assert os.path.getsize(out) > 0

    def test_empty_netlist_raises_valueerror(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "empty.png"
            with pytest.raises(ValueError, match="no components"):
                draw_schematic(EMPTY_NETLIST, out, fmt="png")


# ==================== Integration tests ====================


class TestSchematicIntegration:
    def test_server_draw_schematic_creates_file(self):
        result = create_circuit(RC_LOWPASS)
        assert result["status"] == "ok"
        cid = result["circuit_id"]

        schem = server_draw_schematic(cid, fmt="png")
        assert schem["status"] == "ok"
        assert schem["format"] == "png"
        # filepath is now a bare filename (no absolute path leak)
        assert "/" not in schem["filepath"]
        assert schem["filepath"] == "schematic.png"

    def test_server_draw_schematic_invalid_id(self):
        result = server_draw_schematic("nonexistent", fmt="png")
        assert result["status"] == "error"
        assert "not found" in result["error"]


def test_no_matplotlib_in_source():
    """Verify schematic.py does not directly import matplotlib."""
    import ast
    import inspect

    from spicebridge import schematic

    source = inspect.getsource(schematic)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("matplotlib"), (
                    f"schematic.py imports matplotlib: {alias.name}"
                )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("matplotlib")
        ):
            raise AssertionError(f"schematic.py imports from matplotlib: {node.module}")
