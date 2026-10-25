"""Tests for spicebridge.kicad_export — KiCad 8 schematic export."""

import math
import re
import tempfile
from pathlib import Path

import pytest

from spicebridge.kicad_export import (
    _LIB_SYMBOL_TEMPLATES,
    _SYMBOL_MAP,
    _build_lib_symbols,
    _find_ground_pins,
    _layout_components,
    _resolve_symbol_info,
    _route_wires,
    _snap_to_grid,
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
        assert info.lib_id == "Device:Q_NPN_BCE"
        assert len(info.pin_numbers) == 3

    def test_pnp_heuristic(self):
        info = _resolve_symbol_info("Q", "PNP_MODEL")
        assert info.lib_id == "Device:Q_PNP_BCE"

    def test_pnp_case_insensitive(self):
        info = _resolve_symbol_info("Q", "some_pnp_transistor")
        assert info.lib_id == "Device:Q_PNP_BCE"

    def test_nmos_default(self):
        info = _resolve_symbol_info("M", "NMOS_MODEL")
        assert info.lib_id == "Device:Q_NMOS_GDS"
        assert len(info.pin_numbers) == 3

    def test_pmos_heuristic(self):
        info = _resolve_symbol_info("M", "PMOS_MODEL")
        assert info.lib_id == "Device:Q_PMOS_GDS"

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
        for s in sources:
            assert s.x == 50.8

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
        assert "Q_NPN_BCE" in content

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
            assert "Q_NMOS_GDS" in content

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
    "X": ["1", "2", "3", "4", "5"],
}

DIODE_SUBCKT_CIRCUIT = """\
* Diode clamp with subcircuit
V1 in 0 dc 5
R1 in a 1k
D1 a k 1N4148
R2 k 0 10k
X1 a k out 0 mysub
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
    tree = _sexp("(root " + _build_lib_symbols(set(_LIB_SYMBOL_TEMPLATES)) + ")")
    lib_pins = _lib_pin_positions(tree)
    infos = [*_SYMBOL_MAP.values(), _resolve_symbol_info("X", "sub")]
    for info in infos:
        pins = lib_pins[info.lib_id]
        for number, (dx, dy) in zip(info.pin_numbers, info.pin_offsets, strict=True):
            lx, ly = pins[number]
            assert (dx, dy) == pytest.approx((lx, -ly)), (info.lib_id, number)
