"""Tests for spicebridge.kicad_export — KiCad 8 schematic export."""

import math
import re
import tempfile
from pathlib import Path

import pytest

from spicebridge.kicad_export import (
    _SYMBOL_MAP,
    _body_box,
    _build_lib_symbols,
    _find_ground_pins,
    _find_net_labels,
    _layout_components,
    _model_types,
    _obstacle_boxes,
    _pin_directions,
    _pin_positions,
    _resolve_symbol_info,
    _route_nets,
    _route_wires,
    _snap_to_grid,
    _subckt_box,
    _subckt_ports,
    export_kicad_schematic,
)
from spicebridge.schematic import parse_netlist
from spicebridge.server import create_circuit
from spicebridge.server import export_kicad as server_export_kicad

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

MOSFET_CIRCUIT = """\
* NMOS inverter
V1 vdd 0 dc 5
M1 vdd gate drain source NMOS_MODEL
R1 vdd drain 1k
"""

PNP_CIRCUIT = """\
* PNP amplifier
V1 vcc 0 dc 12
Q1 vcc base emitter PNP_MODEL
R1 base 0 10k
"""


# ==================== Symbol mapping tests ====================


class TestSymbolMapping:
    def test_resistor_mapping(self):
        info = _resolve_symbol_info("R", "1k")
        assert info.lib_id == "Device:R"
        assert len(info.pin_numbers) == 2

    def test_capacitor_mapping(self):
        info = _resolve_symbol_info("C", "100n")
        assert info.lib_id == "Device:C"
        assert len(info.pin_numbers) == 2

    def test_inductor_mapping(self):
        info = _resolve_symbol_info("L", "10m")
        assert info.lib_id == "Device:L"
        assert len(info.pin_numbers) == 2

    def test_diode_mapping(self):
        info = _resolve_symbol_info("D", "1N4148")
        assert info.lib_id == "Device:D"
        # SPICE diode node order is anode, cathode
        assert info.pin_numbers == ["A", "K"]

    def test_voltage_source_mapping(self):
        info = _resolve_symbol_info("V", "dc 5")
        assert info.lib_id == "Simulation_SPICE:VDC"
        assert len(info.pin_numbers) == 2

    def test_current_source_mapping(self):
        info = _resolve_symbol_info("I", "dc 1m")
        assert info.lib_id == "Simulation_SPICE:IDC"
        assert len(info.pin_numbers) == 2

    def test_npn_default(self):
        info = _resolve_symbol_info("Q", "2N2222")
        assert info.lib_id == "Device:Q_NPN"
        assert len(info.pin_numbers) == 3

    def test_pnp_heuristic(self):
        info = _resolve_symbol_info("Q", "PNP_MODEL")
        assert info.lib_id == "Device:Q_PNP"

    def test_pnp_case_insensitive(self):
        info = _resolve_symbol_info("Q", "some_pnp_transistor")
        assert info.lib_id == "Device:Q_PNP"

    def test_nmos_default(self):
        info = _resolve_symbol_info("M", "NMOS_MODEL")
        assert info.lib_id == "Device:Q_NMOS"
        assert len(info.pin_numbers) == 3

    def test_pmos_heuristic(self):
        info = _resolve_symbol_info("M", "PMOS_MODEL")
        assert info.lib_id == "Device:Q_PMOS"

    def test_subcircuit_mapping(self):
        info = _resolve_symbol_info("X", "OPAMP")
        assert info.lib_id == "Simulation_SPICE:SUBCKT"

    def test_all_basic_types_mapped(self):
        for comp_type in ("R", "C", "L", "D", "V", "I", "Q", "M", "X"):
            info = _resolve_symbol_info(comp_type, "test")
            assert info.lib_id
            assert len(info.pin_numbers) >= 2


# ==================== Layout tests ====================


class TestLayout:
    def test_correct_component_count(self):
        comps = parse_netlist(RC_LOWPASS)
        placed = _layout_components(comps)
        assert len(placed) == len(comps)

    def test_no_overlapping_positions(self):
        comps = parse_netlist(BJT_AMPLIFIER)
        placed = _layout_components(comps)
        positions = [(p.x, p.y) for p in placed]
        assert len(positions) == len(set(positions))

    def test_grid_alignment(self):
        comps = parse_netlist(RC_LOWPASS)
        placed = _layout_components(comps)
        grid = 2.54
        for p in placed:
            x_rem = p.x % grid
            y_rem = p.y % grid
            assert min(x_rem, grid - x_rem) < 0.01, f"x={p.x} not on grid"
            assert min(y_rem, grid - y_rem) < 0.01, f"y={p.y} not on grid"

    def test_sources_in_first_column(self):
        comps = parse_netlist(RC_LOWPASS)
        placed = _layout_components(comps)
        sources = [p for p in placed if p.component.comp_type in ("V", "I")]
        assert sources
        for s in sources:
            assert s.x == min(p.x for p in placed)

    def test_snap_to_grid(self):
        assert _snap_to_grid(2.53) == 2.54
        assert _snap_to_grid(2.55) == 2.54
        assert _snap_to_grid(5.08) == 5.08


# ==================== Wire routing tests ====================


class TestWireRouting:
    def test_wires_generated(self):
        comps = parse_netlist(RC_LOWPASS)
        placed = _layout_components(comps)
        wires, _junctions = _route_wires(placed)
        assert len(wires) > 0

    def test_all_manhattan(self):
        comps = parse_netlist(RC_LOWPASS)
        placed = _layout_components(comps)
        wires, _junctions = _route_wires(placed)
        for w in wires:
            x1, y1 = w.start
            x2, y2 = w.end
            assert x1 == x2 or y1 == y2, f"Non-Manhattan wire: {w}"

    def test_ground_produces_power_symbols(self):
        comps = parse_netlist(RC_LOWPASS)
        placed = _layout_components(comps)
        ground_pins = _find_ground_pins(placed)
        assert len(ground_pins) > 0


# ==================== S-expression output tests ====================


class TestSExpression:
    def _export(self, netlist: str, filename: str = "test.kicad_sch") -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _warnings = export_kicad_schematic(
                netlist, output_dir=Path(tmpdir), filename=filename
            )
            return path.read_text(encoding="utf-8")

    def test_valid_structure(self):
        content = self._export(RC_LOWPASS)
        assert content.startswith("(kicad_sch")
        assert content.rstrip().endswith(")")

    def test_version_header(self):
        content = self._export(RC_LOWPASS)
        assert "(version 20231120)" in content
        assert '(generator "spicebridge")' in content

    def test_contains_lib_symbols(self):
        content = self._export(RC_LOWPASS)
        assert "(lib_symbols" in content

    def test_symbol_instances_with_ref_and_value(self):
        content = self._export(RC_LOWPASS)
        assert '"R1"' in content
        assert '"V1"' in content
        assert '"C1"' in content

    def test_wires_present(self):
        content = self._export(RC_LOWPASS)
        assert "(wire" in content

    def test_gnd_symbols(self):
        content = self._export(RC_LOWPASS)
        assert '"power:GND"' in content

    def test_sheet_instances(self):
        content = self._export(RC_LOWPASS)
        assert "(sheet_instances" in content

    def test_all_uuids_unique(self):
        content = self._export(RC_LOWPASS)
        uuids = re.findall(
            r'"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', content
        )
        assert len(uuids) > 0
        assert len(uuids) == len(set(uuids)), "Duplicate UUIDs found"

    def test_balanced_parentheses(self):
        content = self._export(RC_LOWPASS)
        open_count = content.count("(")
        close_count = content.count(")")
        assert open_count == close_count, (
            f"Unbalanced parens: {open_count} open vs {close_count} close"
        )

    def test_custom_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = export_kicad_schematic(
                RC_LOWPASS, output_dir=Path(tmpdir), filename="custom.kicad_sch"
            )
            assert path.name == "custom.kicad_sch"
            assert path.exists()

    def test_empty_netlist_raises(self):
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            pytest.raises(ValueError, match="no components"),
        ):
            export_kicad_schematic(EMPTY_NETLIST, output_dir=Path(tmpdir))

    def test_voltage_divider_export(self):
        content = self._export(VOLTAGE_DIVIDER)
        assert '"R1"' in content
        assert '"R2"' in content
        assert '"V1"' in content

    def test_bjt_export(self):
        content = self._export(BJT_AMPLIFIER)
        assert '"Q1"' in content
        assert "Q_NPN" in content

    def test_net_labels(self):
        content = self._export(RC_LOWPASS)
        assert "(label" in content


# ==================== MOSFET handling tests ====================


class TestMosfetHandling:
    def test_mosfet_4pin_to_3pin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path, warnings = export_kicad_schematic(
                MOSFET_CIRCUIT, output_dir=Path(tmpdir)
            )
            content = path.read_text(encoding="utf-8")
            assert "Q_NMOS" in content

    def test_mosfet_bulk_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _path, warnings = export_kicad_schematic(
                MOSFET_CIRCUIT, output_dir=Path(tmpdir)
            )
            bulk_warnings = [w for w in warnings if "bulk" in w.lower()]
            assert len(bulk_warnings) > 0


# ==================== Integration tests ====================


class TestIntegration:
    def test_server_export_creates_file(self):
        result = create_circuit(RC_LOWPASS)
        assert result["status"] == "ok"
        cid = result["circuit_id"]

        export = server_export_kicad(cid)
        assert export["status"] == "ok"
        assert export["num_components"] == 3
        # file_path is now a bare filename (no absolute path leak)
        assert "/" not in export["file_path"]
        assert export["file_path"].endswith(".kicad_sch")

    def test_server_export_returns_kicad_content(self):
        result = create_circuit(RC_LOWPASS)
        cid = result["circuit_id"]

        export = server_export_kicad(cid)
        assert export["status"] == "ok"
        assert "kicad_content" in export
        assert export["kicad_content"].startswith("(kicad_sch")

    def test_server_export_invalid_id(self):
        result = server_export_kicad("nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_server_export_custom_filename(self):
        result = create_circuit(RC_LOWPASS)
        cid = result["circuit_id"]

        export = server_export_kicad(cid, filename="my_circuit.kicad_sch")
        assert export["status"] == "ok"
        assert "my_circuit.kicad_sch" in export["file_path"]

    def test_server_export_bjt(self):
        result = create_circuit(BJT_AMPLIFIER)
        assert result["status"] == "ok"
        cid = result["circuit_id"]

        export = server_export_kicad(cid)
        assert export["status"] == "ok"
        assert export["num_components"] == 5

    def test_public_api_import(self):
        from spicebridge import export_kicad_schematic as fn

        assert callable(fn)


# --- Regression tests for issue #5 ---

ISSUE5_NETLIST = """\
Sallen-Key lowpass Butterworth fc~1kHz, op-amp with negative feedback
V1 in 0 dc 0 ac 1
R1 in n1 10k
R2 n1 n2 10k
C1 n1 out 22n
C2 n2 0 10n
E1 out 0 n2 out 100k
.ac dec 20 10 100k
.end
"""

_PRIMITIVE_CHILDREN = {
    "pts",
    "stroke",
    "fill",
    "start",
    "mid",
    "end",
    "center",
    "radius",
}


def _sexp(text: str):
    """Minimal S-expression parser (strings kept as str, atoms as str)."""
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+', text)
    stack: list[list] = [[]]
    for tok in tokens:
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(tok)
    assert len(stack) == 1, "unbalanced parentheses"
    return stack[0][0]


def _children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


@pytest.fixture
def issue5_tree(tmp_path):
    path, _ = export_kicad_schematic(ISSUE5_NETLIST, output_dir=tmp_path)
    return _sexp(path.read_text())


class TestIssue5:
    def test_lib_symbols_only_contain_symbols(self, issue5_tree):
        (lib,) = _children(issue5_tree, "lib_symbols")
        names = [c[1] for c in lib[1:]]
        assert all(c[0] == "symbol" for c in lib[1:])
        assert '"power:GND"' in names

    def test_drawing_primitives_are_closed(self, issue5_tree):
        (lib,) = _children(issue5_tree, "lib_symbols")
        for sym in lib[1:]:
            for unit in _children(sym, "symbol"):
                for prim in unit[2:]:
                    if prim[0] == "pin":
                        continue
                    for child in prim[1:]:
                        assert child[0] in _PRIMITIVE_CHILDREN, (
                            sym[1],
                            prim[0],
                            child[0],
                        )

    def test_placed_symbols_pin_before_instances_and_no_pin_names(self, issue5_tree):
        placed = _children(issue5_tree, "symbol")
        assert placed
        for sym in placed:
            keys = [c[0] for c in sym[1:] if isinstance(c, list)]
            assert "pin_names" not in keys
            assert "instances" in keys
            pin_idx = [i for i, k in enumerate(keys) if k == "pin"]
            assert pin_idx and max(pin_idx) < keys.index("instances")

    def test_title_line_not_parsed_as_component(self, issue5_tree):
        refs = []
        for sym in _children(issue5_tree, "symbol"):
            for prop in _children(sym, "property"):
                if prop[1] == '"Reference"':
                    refs.append(prop[2].strip('"'))
        assert "Sallen-Key" not in refs
        assert {"V1", "R1", "R2", "C1", "C2", "E1"} <= set(refs)


def test_subckt_body_not_parsed_as_components():
    netlist = """\
* opamp test
.subckt ideal_opamp inp inn out
E1 out 0 inp inn 100k
.ends ideal_opamp
V1 in 0 dc 1
X1 in out out ideal_opamp
"""
    refs = [c.ref for c in parse_netlist(netlist)]
    assert refs == ["V1", "X1"]


# --- Connectivity: exported schematic must match the SPICE netlist ---

# SPICE node order -> KiCad pin number, independent of _SYMBOL_MAP
_SPICE_PIN_ORDER = {
    "D": ["A", "K"],
    "Q": ["C", "B", "E"],
    "M": ["D", "G", "S"],
    "J": ["1", "2", "3"],
    "E": ["1", "2", "3", "4"],
    "G": ["1", "2", "3", "4"],
    "X": [str(i) for i in range(1, 21)],
}

DIODE_SUBCKT_CIRCUIT = """\
* Diode clamp with subcircuit
V1 in 0 dc 5
R1 in a 1k
D1 a k 1N4148
R2 k 0 10k
X1 a k out 0 mysub
"""

CONTROLLED_SOURCES_CIRCUIT = """\
* Controlled and behavioral sources
V1 in 0 dc 1
R1 in a 1k
E1 b 0 a 0 10
R2 b c 1k
G1 c 0 a b 1m
Vsense c d dc 0
F1 d 0 Vsense 2
H1 e 0 Vsense 100
R3 e d 1k
B1 f 0 V=V(a)*2
R4 f a 1k
"""

JFET_CIRCUIT = """\
* JFET pair, polarity from .model cards
.model JN njf(vto=-2)
.model JP pjf(vto=2)
V1 vdd 0 dc 9
J1 vdd gate src JN
J2 0 gate src JP
R1 src 0 1k
R2 vdd gate 1meg
"""

WIDE_SUBCKT_CIRCUIT = """\
* Subcircuit with nine ports
V1 a 0 dc 1
R1 a b 1k
X1 a b c d e f g h 0 big
R2 h 0 1k
R3 c d 1k
"""

BEHAVIORAL_CIRCUIT = """\
* Behavioral E/G forms
V1 a 0 dc 1
R1 a b 1k
E1 b 0 VALUE={V(a)*2}
G1 c 0 cur=V(a)*1m
R2 c 0 1k
E2 d 0 POLY(1) a 0 0 3
R3 d b 1k
"""

NAMED_SUBCKT_CIRCUIT = """\
* Subcircuits with named ports
.subckt opamp3 inp inn
+ out params: gain=1e5
E1 out 0 inp inn {gain}
.ends opamp3
.subckt filter_stage in out gnd
R1 in out 1k
C1 out gnd 1n
.ends
V1 in 0 dc 1
X1 in fb out opamp3 gain=1e4
R1 out fb 10k
X2 out filt 0 filter_stage
R2 filt 0 1k
"""

COUPLED_INDUCTORS_CIRCUIT = """\
* Transformer
V1 a 0 ac 1
L1 a 0 1m
L2 b 0 1m
K1 L1 L2 0.9
R1 b 0 1k
"""


def _lib_pin_positions(tree) -> dict[str, dict[str, tuple[float, float]]]:
    """Map lib_id -> pin number -> pin position in lib coordinates (Y up)."""
    (lib,) = _children(tree, "lib_symbols")
    result: dict[str, dict[str, tuple[float, float]]] = {}
    for sym in lib[1:]:
        pins: dict[str, tuple[float, float]] = {}
        for unit in _children(sym, "symbol"):
            for pin in _children(unit, "pin"):
                (at,) = _children(pin, "at")
                (number,) = _children(pin, "number")
                pins[number[1].strip('"')] = (float(at[1]), float(at[2]))
        result[sym[1].strip('"')] = pins
    return result


def _kicad_nets(tree) -> list[set[str]]:
    """Compute connectivity the way KiCad does: pins, wires, labels, GND."""
    parent: dict = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    def pt(x, y):
        return (round(float(x), 3), round(float(y), 3))

    lib_pins = _lib_pin_positions(tree)
    points: list[tuple[float, float]] = []

    for sym in _children(tree, "symbol"):
        lib_id = _children(sym, "lib_id")[0][1].strip('"')
        _, x, y, rot = _children(sym, "at")[0]
        x, y, a = float(x), float(y), math.radians(float(rot))
        ref = next(
            p[2].strip('"') for p in _children(sym, "property") if p[1] == '"Reference"'
        )
        for number, (lx, ly) in lib_pins[lib_id].items():
            sx, sy = lx, -ly  # lib Y up -> schematic Y down
            px = x + sx * math.cos(a) + sy * math.sin(a)
            py = y - sx * math.sin(a) + sy * math.cos(a)
            p = pt(px, py)
            points.append(p)
            union(p, "GND" if lib_id == "power:GND" else f"{ref}.{number}")

    wires = []
    for w in _children(tree, "wire"):
        (pts,) = _children(w, "pts")
        a, b = pt(*pts[1][1:3]), pt(*pts[2][1:3])
        wires.append((a, b))
        points += [a, b]
        union(a, b)

    for lbl in _children(tree, "label"):
        p = pt(*_children(lbl, "at")[0][1:3])
        points.append(p)
        union(p, "label:" + lbl[1].strip('"'))

    # Anything touching a wire segment is connected to it
    for (x1, y1), (x2, y2) in wires:
        for p in points:
            if min(x1, x2) <= p[0] <= max(x1, x2) and min(y1, y2) <= p[1] <= max(
                y1, y2
            ):
                union(p, (x1, y1))

    groups: dict = {}
    for key in list(parent):
        if isinstance(key, str) and "." in key and not key.startswith("label:"):
            groups.setdefault(find(key), set()).add(key)
    return list(groups.values())


def _expected_nets(netlist: str) -> list[set[str]]:
    nets: dict[str, set[str]] = {}
    for comp in parse_netlist(netlist):
        if comp.comp_type == "K":  # coupling only, not drawn
            continue
        order = _SPICE_PIN_ORDER.get(comp.comp_type, ["1", "2"])
        for node, number in zip(comp.nodes, order, strict=False):
            key = "0" if node.lower() in ("0", "gnd") else node
            nets.setdefault(key, set()).add(f"{comp.ref}.{number}")
    return list(nets.values())


@pytest.mark.parametrize(
    "netlist",
    [
        ISSUE5_NETLIST,
        RC_LOWPASS,
        VOLTAGE_DIVIDER,
        BJT_AMPLIFIER,
        MOSFET_CIRCUIT,
        PNP_CIRCUIT,
        DIODE_SUBCKT_CIRCUIT,
        CONTROLLED_SOURCES_CIRCUIT,
        JFET_CIRCUIT,
        WIDE_SUBCKT_CIRCUIT,
        COUPLED_INDUCTORS_CIRCUIT,
        BEHAVIORAL_CIRCUIT,
        NAMED_SUBCKT_CIRCUIT,
    ],
)
def test_exported_connectivity_matches_netlist(netlist, tmp_path):
    path, _ = export_kicad_schematic(netlist, output_dir=tmp_path)
    expected = sorted(sorted(g) for g in _expected_nets(netlist))
    used = {pin for group in expected for pin in group}
    # Symbol pins without a SPICE node (e.g. spare SUBCKT pins) are ignored
    actual = sorted(
        sorted(g & used) for g in _kicad_nets(_sexp(path.read_text())) if g & used
    )
    assert actual == expected


def test_symbol_map_offsets_match_lib_templates():
    infos = [*_SYMBOL_MAP.values()]
    infos += [_resolve_symbol_info("X", "sub", n_nodes=n) for n in (2, 5, 6, 9)]
    ports = {"named": ["in", "out", "very_long_port_name"]}
    infos.append(_resolve_symbol_info("X", "named", n_nodes=3, subckt_ports=ports))
    lib_ids = {info.lib_id for info in infos}
    pin_names = {i.lib_id: i.pin_names for i in infos if i.pin_names}
    tree = _sexp("(root " + _build_lib_symbols(lib_ids, False, pin_names) + ")")
    lib_pins = _lib_pin_positions(tree)
    assert set(lib_pins) == lib_ids
    for info in infos:
        pins = lib_pins[info.lib_id]
        for number, (dx, dy) in zip(info.pin_numbers, info.pin_offsets, strict=True):
            lx, ly = pins[number]
            assert (dx, dy) == pytest.approx((lx, -ly)), (info.lib_id, number)


# --- Symbols, warnings and layout for the less common element types ---


def _placed_lib_ids(netlist, tmp_path) -> dict[str, str]:
    path, _ = export_kicad_schematic(netlist, output_dir=tmp_path)
    tree = _sexp(path.read_text())
    result = {}
    for sym in _children(tree, "symbol"):
        ref = next(
            p[2].strip('"') for p in _children(sym, "property") if p[1] == '"Reference"'
        )
        result[ref] = _children(sym, "lib_id")[0][1].strip('"')
    return result


def test_controlled_sources_have_own_symbols(tmp_path):
    lib_ids = _placed_lib_ids(CONTROLLED_SOURCES_CIRCUIT, tmp_path)
    assert lib_ids["E1"] == "Simulation_SPICE:ESOURCE"
    assert lib_ids["G1"] == "Simulation_SPICE:GSOURCE"
    assert lib_ids["F1"] == "Simulation_SPICE:FSOURCE"
    assert lib_ids["H1"] == "Simulation_SPICE:HSOURCE"
    assert lib_ids["B1"] == "Simulation_SPICE:BSOURCE"


def test_jfet_polarity_from_model_card(tmp_path):
    lib_ids = _placed_lib_ids(JFET_CIRCUIT, tmp_path)
    assert lib_ids["J1"] == "Simulation_SPICE:NJFET"
    assert lib_ids["J2"] == "Simulation_SPICE:PJFET"


def test_bjt_polarity_from_model_card(tmp_path):
    netlist = PNP_CIRCUIT.replace("PNP_MODEL", "2N3906") + ".model 2N3906 PNP(BF=200)\n"
    assert _placed_lib_ids(netlist, tmp_path)["Q1"] == "Device:Q_PNP"


def test_wide_subckt_gets_generated_symbol(tmp_path):
    lib_ids = _placed_lib_ids(WIDE_SUBCKT_CIRCUIT, tmp_path)
    assert lib_ids["X1"] == "Simulation_SPICE:SUBCKT_9"


def test_warnings_for_skipped_and_placeholder_elements(tmp_path):
    netlist = COUPLED_INDUCTORS_CIRCUIT + "T1 b 0 c 0 Z0=50 TD=1n\nR2 c 0 50\n"
    _, warnings = export_kicad_schematic(netlist, output_dir=tmp_path)
    assert any(w.startswith("K1:") for w in warnings)
    assert any(w.startswith("T1:") and "placeholder" in w for w in warnings)
    assert not any(w.startswith(("L1:", "R1:", "V1:")) for w in warnings)


_LAYOUT_CIRCUITS = [
    ISSUE5_NETLIST,
    BJT_AMPLIFIER,
    CONTROLLED_SOURCES_CIRCUIT,
    JFET_CIRCUIT,
    WIDE_SUBCKT_CIRCUIT,
    DIODE_SUBCKT_CIRCUIT,
    BEHAVIORAL_CIRCUIT,
    NAMED_SUBCKT_CIRCUIT,
]


def _crosses_box(wire, box, eps: float = 1e-6) -> bool:
    """Return True if the axis-aligned wire runs through the box interior."""
    (x1, y1), (x2, y2) = wire.start, wire.end
    bx1, by1, bx2, by2 = box
    lo_x, hi_x = max(min(x1, x2), bx1), min(max(x1, x2), bx2)
    lo_y, hi_y = max(min(y1, y2), by1), min(max(y1, y2), by2)
    if y1 == y2:
        return by1 + eps < y1 < by2 - eps and lo_x < hi_x - eps
    return bx1 + eps < x1 < bx2 - eps and lo_y < hi_y - eps


def _placed(netlist):
    comps = [c for c in parse_netlist(netlist) if c.comp_type != "K"]
    return _layout_components(comps, _model_types(netlist), _subckt_ports(netlist))


@pytest.mark.parametrize("netlist", _LAYOUT_CIRCUITS)
def test_component_bodies_do_not_overlap(netlist):
    boxes = [_body_box(pc) for pc in _placed(netlist)]
    for i, (ax1, ay1, ax2, ay2) in enumerate(boxes):
        for bx1, by1, bx2, by2 in boxes[i + 1 :]:
            overlap = ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2
            assert not overlap


@pytest.mark.parametrize("netlist", _LAYOUT_CIRCUITS)
def test_no_wire_crosses_a_component(netlist):
    placed = _placed(netlist)
    wires, _ = _route_wires(placed)
    for wire in wires:
        for pc in placed:
            for box in _obstacle_boxes(pc):
                assert not _crosses_box(wire, box), (wire, pc.component.ref)


_DIRECTION_VECTORS = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


@pytest.mark.parametrize("netlist", _LAYOUT_CIRCUITS)
def test_labels_and_ground_point_away_from_component(netlist):
    placed = _placed(netlist)
    origins = {}
    for pc in placed:
        for pos in _pin_positions(pc):
            origins[pos] = (pc.x, pc.y)
    every_net = {node for pc in placed for node in pc.component.nodes}
    labels = _find_net_labels(placed, every_net)
    markers = [(x, y, d) for x, y, _name, d in labels]
    markers += _find_ground_pins(placed)
    assert markers
    for x, y, direction in markers:
        ox, oy = origins[(x, y)]
        dx, dy = _DIRECTION_VECTORS[direction]
        # Pointing towards the component origin would draw over the body
        assert (ox - x) * dx + (oy - y) * dy <= 0, (x, y, direction)


# --- Behavioral sources and named subcircuit pins ---


def _lib_pin_names(tree, lib_id: str) -> list[str]:
    (lib,) = _children(tree, "lib_symbols")
    (sym,) = [s for s in lib[1:] if s[1].strip(chr(34)) == lib_id]
    names = []
    for unit in _children(sym, "symbol"):
        for pin in _children(unit, "pin"):
            (name,) = _children(pin, "name")
            names.append(name[1].strip(chr(34)))
    return names


def test_behavioral_sources_use_two_pin_symbol(tmp_path):
    lib_ids = _placed_lib_ids(BEHAVIORAL_CIRCUIT, tmp_path)
    assert lib_ids["E1"] == "Simulation_SPICE:BSOURCE"
    assert lib_ids["G1"] == "Simulation_SPICE:BSOURCE"
    assert lib_ids["E2"] == "Simulation_SPICE:BSOURCE"


def test_poly_source_warns_about_control_nodes(tmp_path):
    _, warnings = export_kicad_schematic(BEHAVIORAL_CIRCUIT, output_dir=tmp_path)
    assert [w for w in warnings if w.startswith("E2:")]
    assert not [w for w in warnings if w.startswith(("E1:", "G1:"))]


def test_subckt_pins_named_after_ports(tmp_path):
    path, warnings = export_kicad_schematic(NAMED_SUBCKT_CIRCUIT, output_dir=tmp_path)
    tree = _sexp(path.read_text())
    lib_ids = _placed_lib_ids(NAMED_SUBCKT_CIRCUIT, tmp_path)
    assert lib_ids["X1"] == "Subcircuit:opamp3"
    assert lib_ids["X2"] == "Subcircuit:filter_stage"
    # Continuation lines and params: are handled
    assert _lib_pin_names(tree, "Subcircuit:opamp3") == ["inp", "inn", "out"]
    assert _lib_pin_names(tree, "Subcircuit:filter_stage") == ["in", "out", "gnd"]
    assert warnings == []


def test_subckt_port_count_mismatch_falls_back_to_numbers(tmp_path):
    netlist = NAMED_SUBCKT_CIRCUIT.replace("X2 out filt 0", "X2 out filt")
    _, warnings = export_kicad_schematic(netlist, output_dir=tmp_path)
    assert any(w.startswith("X2:") and "3 ports" in w for w in warnings)
    assert _placed_lib_ids(netlist, tmp_path)["X2"] == "Simulation_SPICE:SUBCKT"


def test_undefined_subckt_keeps_numbered_pins(tmp_path):
    assert _placed_lib_ids(DIODE_SUBCKT_CIRCUIT, tmp_path)["X1"] == (
        "Simulation_SPICE:SUBCKT"
    )


def test_subckt_box_fits_long_pin_names():
    short = _subckt_box(3, ["a", "b", "c"])
    long = _subckt_box(3, ["a", "b", "a_very_long_port_name"])
    assert short[0] == pytest.approx(5.08)
    assert long[0] > short[0]
    assert (long[0] + 5.08) >= len("a_very_long_port_name") * 1.27


def test_value_with_quotes_is_escaped(tmp_path):
    netlist = '* t\nV1 a 0 dc 1\nB1 b 0 V="V(a)"\nR1 b 0 1k\n'
    path, _ = export_kicad_schematic(netlist, output_dir=tmp_path)
    _sexp(path.read_text())  # parses, i.e. quotes did not break the file


# --- Signal-flow layout and routing ---

OPAMP_CASCADE_CIRCUIT = """\
* Two inverting stages with pull-ups to a supply
V1 in 0 ac 1
V2 vcc 0 dc 15
R1 in m0 10k
R2 m0 o0 22k
C1 m0 o0 1n
E1 o0 0 0 m0 1e5
R3 o0 vcc 100k
R4 o0 m1 10k
R5 m1 o1 22k
E2 o1 0 0 m1 1e5
R6 o1 vcc 100k
"""


def _ladder(n: int) -> str:
    lines = ["* RC ladder", "V1 n0 0 ac 1"]
    for i in range(n):
        lines += [f"R{i + 1} n{i} n{i + 1} 1k", f"C{i + 1} n{i + 1} 0 1n"]
    return "\n".join(lines) + "\n"


def _by_ref(placed):
    return {pc.component.ref: pc for pc in placed}


@pytest.mark.parametrize(
    "netlist", [*_LAYOUT_CIRCUITS, RC_LOWPASS, VOLTAGE_DIVIDER, OPAMP_CASCADE_CIRCUIT]
)
def test_router_wires_every_net(netlist):
    _, _, unrouted = _route_nets(_placed(netlist))
    assert unrouted == set()


def test_parts_follow_the_signal_flow():
    parts = _by_ref(_placed(ISSUE5_NETLIST))
    assert parts["V1"].x < parts["R1"].x < parts["R2"].x < parts["E1"].x
    # The feedback capacitor spans n1 -> out above the main row
    assert parts["C1"].y < parts["R1"].y
    # The capacitor to ground hangs below the main row
    assert parts["C2"].y > parts["R2"].y


def test_supply_rail_does_not_merge_stages():
    parts = _by_ref(_placed(OPAMP_CASCADE_CIRCUIT))
    # Stage 2 lies right of stage 1 although both outputs pull up to vcc
    assert parts["E1"].x < parts["R4"].x < parts["E2"].x
    # Pull-ups stand above everything else, each above its own output
    for ref in ("R3", "R6"):
        assert parts[ref].y < parts["C1"].y < parts["R1"].y
    assert abs(parts["R3"].x - parts["E1"].x) < abs(parts["R3"].x - parts["E2"].x)
    assert abs(parts["R6"].x - parts["E2"].x) < abs(parts["R6"].x - parts["E1"].x)


def test_controlled_source_inputs_point_left():
    info = _resolve_symbol_info("E", "10", n_nodes=4)
    (cp_x, _), (cm_x, _) = info.pin_offsets[2:4]
    assert cp_x < 0 and cm_x < 0
    e1 = _by_ref(_placed(ISSUE5_NETLIST))["E1"]
    assert _pin_directions(e1)[2:4] == [180, 180]


def test_paper_grows_with_the_drawing(tmp_path):
    path, _ = export_kicad_schematic(RC_LOWPASS, output_dir=tmp_path)
    assert '(paper "A4")' in path.read_text()
    path, _ = export_kicad_schematic(_ladder(30), output_dir=tmp_path)
    assert '(paper "A4")' not in path.read_text()


def test_unroutable_nets_fall_back_to_labels(tmp_path, monkeypatch):
    from spicebridge import kicad_export

    monkeypatch.setattr(kicad_export._Router, "route", lambda *a, **k: None)
    path, _ = export_kicad_schematic(ISSUE5_NETLIST, output_dir=tmp_path)
    tree = _sexp(path.read_text())
    assert not _children(tree, "wire")
    expected = sorted(sorted(g) for g in _expected_nets(ISSUE5_NETLIST))
    used = {pin for group in expected for pin in group}
    actual = sorted(sorted(g & used) for g in _kicad_nets(tree) if g & used)
    assert actual == expected


# --- KiCad ERC: library names and GND driver ---


def test_transistors_use_kicad_9_device_symbols(tmp_path):
    netlist = (
        "* transistors\nV1 vcc 0 dc 12\nQ1 vcc b e 2N2222\nQ2 0 b e PNPX\n"
        "M1 vcc b e e NM\nM2 0 b e e PM\nR1 vcc b 1k\nR2 e 0 1k\n"
        ".model PNPX pnp\n.model NM nmos\n.model PM pmos\n"
    )
    lib_ids = _placed_lib_ids(netlist, tmp_path)
    assert lib_ids["Q1"] == "Device:Q_NPN"
    assert lib_ids["Q2"] == "Device:Q_PNP"
    assert lib_ids["M1"] == "Device:Q_NMOS"
    assert lib_ids["M2"] == "Device:Q_PMOS"


def test_power_flag_drives_the_gnd_net(tmp_path):
    path, _ = export_kicad_schematic(RC_LOWPASS, output_dir=tmp_path)
    tree = _sexp(path.read_text())
    flags = [
        sym
        for sym in _children(tree, "symbol")
        if _children(sym, "lib_id")[0][1] == '"power:PWR_FLAG"'
    ]
    assert len(flags) == 1
    # The flag sits on the same net as the grounded pins of the circuit
    (group,) = [g for g in _kicad_nets(tree) if "#FLG01.1" in g]
    assert {"V1.2", "C1.2"} <= group


def test_no_power_flag_without_ground(tmp_path):
    netlist = "* floating\nV1 a b dc 1\nR1 a b 1k\n"
    path, _ = export_kicad_schematic(netlist, output_dir=tmp_path)
    assert "PWR_FLAG" not in path.read_text()
