"""Unit tests for the SVG renderer."""

import xml.etree.ElementTree as ET

from spicebridge.svg_renderer import render_svg

RC_LOWPASS = """\
* RC Low-Pass Filter
V1 in 0 AC 1
R1 in out 1k
C1 out 0 100n
.end
"""

BJT_AMP = """\
* BJT Amplifier
V1 vcc 0 12
R1 vcc out 1k
Q1 out base 0 2N2222
R2 vcc base 10k
.end
"""

COMMENT_ONLY = """\
* Just a comment
.param x=1
.end
"""


class TestRenderSvgBasic:
    """Basic SVG output tests."""

    def test_produces_valid_svg_string(self):
        svg = render_svg(RC_LOWPASS)
        assert svg.startswith("<svg")
        assert "</svg>" in svg

    def test_svg_is_valid_xml(self):
        svg = render_svg(RC_LOWPASS)
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg") or root.tag == "svg"

    def test_empty_netlist_returns_valid_svg(self):
        svg = render_svg(COMMENT_ONLY)
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg") or root.tag == "svg"

    def test_has_viewbox(self):
        svg = render_svg(RC_LOWPASS)
        root = ET.fromstring(svg)
        assert root.get("viewBox") is not None


class TestComponentIDs:
    """Component identification and data attributes."""

    def test_component_ids_present(self):
        svg = render_svg(RC_LOWPASS)
        assert 'id="component-V1"' in svg
        assert 'id="component-R1"' in svg
        assert 'id="component-C1"' in svg

    def test_data_ref_attributes(self):
        svg = render_svg(RC_LOWPASS)
        assert 'data-ref="V1"' in svg
        assert 'data-ref="R1"' in svg
        assert 'data-ref="C1"' in svg

    def test_data_type_attributes(self):
        svg = render_svg(RC_LOWPASS)
        assert 'data-type="V"' in svg
        assert 'data-type="R"' in svg
        assert 'data-type="C"' in svg

    def test_data_value_attributes(self):
        svg = render_svg(RC_LOWPASS)
        assert 'data-value="AC 1"' in svg
        assert 'data-value="1k"' in svg
        assert 'data-value="100n"' in svg

    def test_component_class(self):
        svg = render_svg(RC_LOWPASS)
        assert 'class="component"' in svg


class TestWiresAndNodes:
    """Wire elements and node dots."""

    def test_wire_elements_present(self):
        svg = render_svg(RC_LOWPASS)
        assert 'class="wire"' in svg

    def test_wire_has_data_node(self):
        svg = render_svg(RC_LOWPASS)
        root = ET.fromstring(svg)
        wires = root.findall(".//*[@class='wire']")
        for w in wires:
            assert w.get("data-node") is not None

    def test_node_dots_present(self):
        # Use BJT circuit which has 3+ connections to some nets
        svg = render_svg(BJT_AMP)
        assert 'class="node-dot"' in svg


class TestGroundAndNetLabels:
    """Ground symbols and net labels."""

    def test_ground_symbols_present(self):
        svg = render_svg(RC_LOWPASS)
        assert 'class="ground-symbol"' in svg

    def test_net_labels_present(self):
        svg = render_svg(RC_LOWPASS)
        # 'in' and 'out' should be labeled
        assert 'class="net-label"' in svg


class TestSimulationOverlay:
    """Simulation result annotation overlay."""

    def test_annotations_with_results(self):
        results = {
            "analysis_type": "Operating Point",
            "nodes": {"in": 1.0, "out": 0.5},
        }
        svg = render_svg(RC_LOWPASS, results=results)
        assert 'class="sim-annotation"' in svg
        assert "1V" in svg or "1.0V" in svg or "1V" in svg

    def test_no_annotations_without_results(self):
        svg = render_svg(RC_LOWPASS, results=None)
        assert "sim-annotation" not in svg or svg.count("sim-annotation") <= 1
        # The class definition in <style> may mention it, but no actual elements


class TestBJTCircuit:
    """Test with a BJT circuit."""

    def test_bjt_renders(self):
        svg = render_svg(BJT_AMP)
        assert 'id="component-Q1"' in svg
        assert 'data-type="Q"' in svg

    def test_bjt_svg_valid_xml(self):
        svg = render_svg(BJT_AMP)
        ET.fromstring(svg)
