"""Tests for spicebridge.kicad_export — KiCad 8 schematic export."""

import re
import tempfile
from pathlib import Path

import pytest

from spicebridge.kicad_export import (
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
        assert info.pin_numbers == ["K", "A"]

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
