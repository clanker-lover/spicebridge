"""Tests for spicebridge.constants and spicebridge.netlist_utils."""

from spicebridge.constants import ANALYSIS_RE, COMPONENT_NODE_COUNTS, END_RE
from spicebridge.netlist_utils import prepare_netlist


def test_component_node_counts_importable():
    """COMPONENT_NODE_COUNTS should have correct entries."""
    assert COMPONENT_NODE_COUNTS["R"] == 2
    assert COMPONENT_NODE_COUNTS["M"] == 4


def test_analysis_re_matches():
    """ANALYSIS_RE should match analysis directives."""
    assert ANALYSIS_RE.match(".ac dec 10 1 1meg")
    assert ANALYSIS_RE.match(".tran 1u 10m")
    assert ANALYSIS_RE.match(".op")
    assert ANALYSIS_RE.match(".dc V1 0 5 0.1")


def test_end_re_matches():
    """END_RE should match .end directive."""
    assert END_RE.match(".end")
    assert END_RE.match("  .END  ")
    assert not END_RE.match(".ends mysubckt")


def test_prepare_netlist():
    """prepare_netlist should strip old analysis and append new."""
    netlist = "Test Circuit\nR1 1 0 1k\n.ac dec 10 1 1meg\n.end\n"
    result = prepare_netlist(netlist, ".op")
    assert ".ac" not in result
    assert ".op" in result
    assert result.endswith(".end\n")
    assert "R1 1 0 1k" in result
