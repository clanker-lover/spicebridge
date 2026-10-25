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


class TestSvgXssEscaping:
    """Verify SVG rendering escapes user-controlled content."""

    def test_svg_escapes_script_in_value(self):
        netlist = "* XSS test\nR1 a 0 <script>alert(1)</script>\n.end\n"
        svg = render_svg(netlist)
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg


SALLEN_KEY = """\
* Sallen-Key lowpass with an ideal op-amp as VCVS
V1 in 0 DC 0 AC 1
R1 in n1 10k
R2 n1 nn 10k
C1 n1 out 22n
C2 nn 0 10n
E1 out 0 nn out 1e6
.end
"""


def _group(root, ref):
    return root.find(f".//*[@id='component-{ref}']")


class TestSharedLayout:
    """The SVG draws the same scene as the KiCad export."""

    def test_wires_match_the_kicad_scene(self):
        from spicebridge.kicad_export import build_scene

        scene = build_scene(SALLEN_KEY)
        root = ET.fromstring(render_svg(SALLEN_KEY))
        drawn = sorted(
            (
                round(float(w.get("x1")) / 8, 2),
                round(float(w.get("y1")) / 8, 2),
                round(float(w.get("x2")) / 8, 2),
                round(float(w.get("y2")) / 8, 2),
                w.get("data-node"),
            )
            for w in root.findall(".//*[@class='wire']")
        )
        expected = sorted((*w.start, *w.end, w.net) for w in scene.wires)
        assert drawn == expected

    def test_controlled_source_has_all_four_pins(self):
        root = ET.fromstring(render_svg(SALLEN_KEY))
        e1 = _group(root, "E1")
        pin_leads = [
            el
            for el in e1.iter()
            if el.tag.endswith("}line") and el.get("class") == "comp-body"
        ]
        assert len(pin_leads) == 4

    def test_net_names_and_values_are_shown(self):
        root = ET.fromstring(render_svg(SALLEN_KEY))
        nets = {t.text for t in root.findall(".//*[@class='net-label']")}
        assert {"in", "n1", "nn", "out"} <= nets
        labels = {t.text for t in root.findall(".//*[@class='comp-label']")}
        assert {"R1", "10k", "C1", "22n", "E1", "1e6"} <= labels

    def test_wires_carry_their_net(self):
        root = ET.fromstring(render_svg(SALLEN_KEY))
        nets = {w.get("data-node") for w in root.findall(".//*[@class='wire']")}
        assert nets and nets <= {"in", "n1", "nn", "out"}

    def test_subcircuit_pin_names_are_drawn(self):
        netlist = (
            "* named ports\n.subckt amp inp inn out\nE1 out 0 inp inn 1e5\n.ends\n"
            "V1 a 0 dc 1\nX1 a b b amp\nR1 b 0 1k\n"
        )
        root = ET.fromstring(render_svg(netlist))
        names = {t.text for t in _group(root, "X1").iter() if t.tag.endswith("text")}
        assert {"inp", "inn", "out"} <= names

    def test_light_theme_uses_kicad_colours(self):
        svg = render_svg(SALLEN_KEY, theme="light")
        assert "#840000" in svg and "#ffffff" in svg

    def test_natural_size(self):
        root = ET.fromstring(render_svg(SALLEN_KEY, width=None, height=None))
        _, _, vb_w, vb_h = (float(v) for v in root.get("viewBox").split())
        assert float(root.get("width")) == vb_w
        assert float(root.get("height")) == vb_h


class TestHoverTargets:
    """Hovering must work inside open outlines and on thin wires."""

    def test_every_component_has_a_hit_area(self):
        root = ET.fromstring(render_svg(SALLEN_KEY))
        for ref in ("V1", "R1", "R2", "C1", "C2", "E1"):
            hits = [
                el for el in _group(root, ref).iter() if el.get("class") == "comp-hit"
            ]
            assert len(hits) == 1, ref

    def test_every_wire_has_a_wide_hit_line_on_the_same_net(self):
        root = ET.fromstring(render_svg(SALLEN_KEY))

        def wires(cls):
            return sorted(
                (w.get("x1"), w.get("y1"), w.get("x2"), w.get("y2"), w.get("data-node"))
                for w in root.iter()
                if w.get("class") == cls
            )

        assert wires("wire") and wires("wire") == wires("wire wire-hit")


class TestOperatingPointOverlay:
    def test_v_node_result_keys_are_annotated(self):
        """run_dc_op reports node voltages as "v(node)"."""
        results = {"nodes": {"v(in)": 1.0, "v(OUT)": 0.5, "i(v1)": 0.0}}
        root = ET.fromstring(render_svg(RC_LOWPASS, results=results))
        notes = {
            el.get("data-node"): el.text
            for el in root.iter()
            if el.get("class") == "sim-annotation"
        }
        assert notes == {"in": "1V", "out": "0.5V"}
