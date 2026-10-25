"""Export SPICE netlists to KiCad 8 schematic (.kicad_sch) files."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from spicebridge.sanitize import safe_path, validate_filename
from spicebridge.schematic import ParsedComponent, _is_ground, parse_netlist

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

_GRID = 2.54  # KiCad grid spacing in mm


@dataclass
class KiCadSymbolInfo:
    """Maps a SPICE component to its KiCad library symbol."""

    lib_id: str
    pin_numbers: list[str]
    pin_offsets: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class PlacedComponent:
    """A component with assigned schematic position."""

    component: ParsedComponent
    x: float
    y: float
    rotation: float
    symbol_info: KiCadSymbolInfo


@dataclass
class Wire:
    """A schematic wire segment."""

    start: tuple[float, float]
    end: tuple[float, float]


# ---------------------------------------------------------------------------
# Symbol mapping
# ---------------------------------------------------------------------------

# pin_numbers follow SPICE node order (e.g. Q: C B E, M: D G S, D: A K).
# pin_offsets are the pin positions of the matching _LIB_SYMBOL_TEMPLATES
# entry converted to schematic coordinates (Y down, i.e. lib y negated).
_SYMBOL_MAP: dict[str, KiCadSymbolInfo] = {
    "R": KiCadSymbolInfo("Device:R", ["1", "2"], [(0, -3.81), (0, 3.81)]),
    "C": KiCadSymbolInfo("Device:C", ["1", "2"], [(0, -2.54), (0, 2.54)]),
    "L": KiCadSymbolInfo("Device:L", ["1", "2"], [(0, -3.81), (0, 3.81)]),
    "D": KiCadSymbolInfo("Device:D", ["A", "K"], [(2.54, 0), (-2.54, 0)]),
    "V": KiCadSymbolInfo("Simulation_SPICE:VDC", ["1", "2"], [(0, -3.81), (0, 3.81)]),
    "I": KiCadSymbolInfo("Simulation_SPICE:IDC", ["1", "2"], [(0, -3.81), (0, 3.81)]),
    "Q_NPN": KiCadSymbolInfo(
        "Device:Q_NPN_BCE",
        ["C", "B", "E"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
    ),
    "Q_PNP": KiCadSymbolInfo(
        "Device:Q_PNP_BCE",
        ["C", "B", "E"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
    ),
    "M_NMOS": KiCadSymbolInfo(
        "Device:Q_NMOS_GDS",
        ["D", "G", "S"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
    ),
    "M_PMOS": KiCadSymbolInfo(
        "Device:Q_PMOS_GDS",
        ["D", "G", "S"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
    ),
}


def _resolve_symbol_info(comp_type: str, value: str) -> KiCadSymbolInfo:
    """Return the KiCadSymbolInfo for a SPICE component type and value."""
    if comp_type == "Q":
        if "pnp" in value.lower():
            return _SYMBOL_MAP["Q_PNP"]
        return _SYMBOL_MAP["Q_NPN"]
    if comp_type == "M":
        if "pmos" in value.lower():
            return _SYMBOL_MAP["M_PMOS"]
        return _SYMBOL_MAP["M_NMOS"]
    if comp_type == "X":
        # Generic subcircuit — box with 5 pins on the left edge
        pin_numbers = [str(i + 1) for i in range(5)]
        return KiCadSymbolInfo(
            "Simulation_SPICE:SUBCKT",
            pin_numbers,
            [(-7.62, -5.08 + i * 2.54) for i in range(5)],
        )
    if comp_type in _SYMBOL_MAP:
        return _SYMBOL_MAP[comp_type]
    # Fallback: treat as 2-pin generic
    return KiCadSymbolInfo("Device:R", ["1", "2"], [(0, -3.81), (0, 3.81)])


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_COL_SOURCES = 50.8
_COL_SERIES = 101.6
_COL_SHUNT = 152.4
_Y_START = 50.8
_Y_SPACING = 15.24


def _snap_to_grid(val: float) -> float:
    """Round a value to the nearest KiCad grid point."""
    return _mm(round(val / _GRID) * _GRID)


def _mm(val: float) -> float:
    """Round a coordinate to 1 µm to strip floating-point noise."""
    return round(val, 4) + 0.0


def _layout_components(
    components: list[ParsedComponent],
) -> list[PlacedComponent]:
    """Assign positions to components using column-based layout."""
    sources: list[ParsedComponent] = []
    series: list[ParsedComponent] = []
    shunt: list[ParsedComponent] = []

    for comp in components:
        if comp.comp_type in ("V", "I"):
            sources.append(comp)
        elif any(_is_ground(n) for n in comp.nodes):
            shunt.append(comp)
        else:
            series.append(comp)

    placed: list[PlacedComponent] = []

    # Column 0: Sources (vertical, rotation 0)
    for i, comp in enumerate(sources):
        sym = _resolve_symbol_info(comp.comp_type, comp.value)
        y = _snap_to_grid(_Y_START + i * _Y_SPACING)
        placed.append(PlacedComponent(comp, _COL_SOURCES, y, 0, sym))

    # Column 1: Series (horizontal, rotation 270)
    for i, comp in enumerate(series):
        sym = _resolve_symbol_info(comp.comp_type, comp.value)
        y = _snap_to_grid(_Y_START + i * _Y_SPACING)
        placed.append(PlacedComponent(comp, _COL_SERIES, y, 270, sym))

    # Column 2: Shunt (vertical, rotation 0)
    for i, comp in enumerate(shunt):
        sym = _resolve_symbol_info(comp.comp_type, comp.value)
        y = _snap_to_grid(_Y_START + i * _Y_SPACING)
        placed.append(PlacedComponent(comp, _COL_SHUNT, y, 0, sym))

    return placed


# ---------------------------------------------------------------------------
# Pin positions
# ---------------------------------------------------------------------------


def _pin_positions(
    placed: PlacedComponent,
) -> list[tuple[float, float]]:
    """Compute absolute pin positions for a placed component.

    Pin offsets are in schematic coordinates (Y down). KiCad rotates symbols
    counter-clockwise on screen, which in Y-down coordinates is
    (x, y) -> (x*cos + y*sin, -x*sin + y*cos). Positions are not snapped to
    the grid: many pins sit on 1.27 mm half-grid points and wires must end
    exactly on them to connect.
    """
    import math

    angle_rad = math.radians(placed.rotation)
    cos_a = round(math.cos(angle_rad), 9)
    sin_a = round(math.sin(angle_rad), 9)

    positions: list[tuple[float, float]] = []
    n_pins = min(len(placed.component.nodes), len(placed.symbol_info.pin_offsets))
    for i in range(n_pins):
        dx, dy = placed.symbol_info.pin_offsets[i]
        rx = dx * cos_a + dy * sin_a
        ry = -dx * sin_a + dy * cos_a
        positions.append((_mm(placed.x + rx), _mm(placed.y + ry)))

    return positions


# ---------------------------------------------------------------------------
# Wire routing
# ---------------------------------------------------------------------------


def _net_pins(
    placed_components: list[PlacedComponent],
) -> dict[str, list[tuple[float, float]]]:
    """Group absolute pin positions by net name (ground excluded)."""
    net_pins: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for pc in placed_components:
        pins = _pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins) and not _is_ground(node):
                net_pins[node].append(pins[idx])
    return net_pins


def _on_segment(p: tuple[float, float], wire: Wire, eps: float = 1e-6) -> bool:
    """Return True if point p lies on the axis-aligned wire (endpoints included)."""
    (x1, y1), (x2, y2) = wire.start, wire.end
    return (
        min(x1, x2) - eps <= p[0] <= max(x1, x2) + eps
        and min(y1, y2) - eps <= p[1] <= max(y1, y2) + eps
    )


def _route_nets(
    placed_components: list[PlacedComponent],
) -> tuple[list[Wire], list[tuple[float, float]], set[str]]:
    """Route wires between pins sharing the same net.

    KiCad connects anything that touches a wire, so a net is only wired if its
    wires touch no pin or wire of another net. Nets that cannot be wired that
    way are returned as unrouted and get a net label on every pin instead.

    Returns (wires, junctions, unrouted_nets).
    """
    net_pins = _net_pins(placed_components)

    # Every pin, including ground pins (GND symbols are placed on them)
    all_pins: list[tuple[str, tuple[float, float]]] = []
    for pc in placed_components:
        pins = _pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins):
                net = "0" if _is_ground(node) else node
                all_pins.append((net, pins[idx]))

    wires: list[Wire] = []
    wire_nets: list[str] = []
    junctions: list[tuple[float, float]] = []
    unrouted: set[str] = set()

    for net, pin_list in net_pins.items():
        if len(pin_list) < 2:
            continue
        # Sort by x then y for consistent routing
        pin_list = sorted(pin_list)
        net_wires: list[Wire] = []
        net_junctions: list[tuple[float, float]] = []
        for i in range(len(pin_list) - 1):
            x1, y1 = pin_list[i]
            x2, y2 = pin_list[i + 1]
            # Manhattan L-route: horizontal then vertical
            if x1 != x2 and y1 != y2:
                mid = (x2, y1)
                net_wires.append(Wire((x1, y1), mid))
                net_wires.append(Wire(mid, (x2, y2)))
            else:
                net_wires.append(Wire((x1, y1), (x2, y2)))
            if i > 0:
                net_junctions.append((x1, y1))

        foreign_points = [p for n, p in all_pins if n != net]
        foreign_points += [
            pt
            for w, n in zip(wires, wire_nets, strict=True)
            if n != net
            for pt in (w.start, w.end)
        ]
        foreign_wires = [w for w, n in zip(wires, wire_nets, strict=True) if n != net]
        conflict = any(
            _on_segment(p, w) for w in net_wires for p in foreign_points
        ) or any(
            _on_segment(pt, fw)
            for w in net_wires
            for pt in (w.start, w.end)
            for fw in foreign_wires
        )
        if conflict:
            unrouted.add(net)
            continue

        wires.extend(net_wires)
        wire_nets.extend([net] * len(net_wires))
        junctions.extend(net_junctions)

    return wires, list(dict.fromkeys(junctions)), unrouted


def _route_wires(
    placed_components: list[PlacedComponent],
) -> tuple[list[Wire], list[tuple[float, float]]]:
    """Route wires between pins sharing the same net.

    Returns (wires, junctions).
    """
    wires, junctions, _unrouted = _route_nets(placed_components)
    return wires, junctions


# ---------------------------------------------------------------------------
# Ground pins
# ---------------------------------------------------------------------------


def _find_ground_pins(
    placed_components: list[PlacedComponent],
) -> list[tuple[float, float]]:
    """Find pin positions connected to ground for placing GND power symbols."""
    ground_positions: list[tuple[float, float]] = []
    for pc in placed_components:
        pins = _pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins) and _is_ground(node):
                ground_positions.append(pins[idx])
    return ground_positions


# ---------------------------------------------------------------------------
# Net labels
# ---------------------------------------------------------------------------


def _find_net_labels(
    placed_components: list[PlacedComponent],
    unrouted: set[str] | None = None,
) -> list[tuple[float, float, str]]:
    """Find named nets (not numbered, not ground) and return label positions.

    Nets in ``unrouted`` have no wires, so every one of their pins is labelled
    (numbered nets included) to connect them by name.
    """
    unrouted = unrouted or set()
    net_pins = _net_pins(placed_components)

    labels: list[tuple[float, float, str]] = []
    for net_name, pin_list in net_pins.items():
        if net_name in unrouted:
            labels.extend((x, y, net_name) for x, y in pin_list)
            continue
        # Skip purely numeric net names
        if net_name.isdigit():
            continue
        if pin_list:
            x, y = pin_list[0]
            labels.append((x, y, net_name))

    return labels


# ---------------------------------------------------------------------------
# Lib symbol templates
# ---------------------------------------------------------------------------

_LIB_SYMBOL_TEMPLATES: dict[str, str] = {
    "Device:R": """
    (symbol "Device:R" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at -2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at -1.778 0 90) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "R_0_1"
        (rectangle (start -1.016 -3.81) (end 1.016 3.81)
          (stroke (width 0) (type default)) (fill (type none))
        )
      )
      (symbol "R_1_1"
        (pin passive line (at 0 3.81 270) (length 0) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 0) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:C": """
    (symbol "Device:C" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "C" (at -2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "C_0_1"
        (polyline (pts (xy -1.524 -0.508) (xy 1.524 -0.508))
          (stroke (width 0.3048) (type default)) (fill (type none))
        )
        (polyline (pts (xy -1.524 0.508) (xy 1.524 0.508))
          (stroke (width 0.3048) (type default)) (fill (type none))
        )
      )
      (symbol "C_1_1"
        (pin passive line (at 0 2.54 270) (length 2.032) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -2.54 90) (length 2.032) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:L": """
    (symbol "Device:L" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "L" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "L" (at -2.032 0 90) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "L_0_1"
        (arc (start 0 -3.81) (mid 0.6323 -3.1777) (end 0 -2.54)
          (stroke (width 0) (type default)) (fill (type none))
        )
        (arc (start 0 -2.54) (mid 0.6323 -1.9077) (end 0 -1.27)
          (stroke (width 0) (type default)) (fill (type none))
        )
        (arc (start 0 -1.27) (mid 0.6323 -0.6377) (end 0 0)
          (stroke (width 0) (type default)) (fill (type none))
        )
        (arc (start 0 0) (mid 0.6323 0.6323) (end 0 1.27)
          (stroke (width 0) (type default)) (fill (type none))
        )
        (arc (start 0 1.27) (mid 0.6323 1.9023) (end 0 2.54)
          (stroke (width 0) (type default)) (fill (type none))
        )
        (arc (start 0 2.54) (mid 0.6323 3.1723) (end 0 3.81)
          (stroke (width 0) (type default)) (fill (type none))
        )
      )
      (symbol "L_1_1"
        (pin passive line (at 0 3.81 270) (length 0) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 0) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:D": """
    (symbol "Device:D" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "D" (at 0 2.54 0) (effects (font (size 1.27 1.27))))
      (property "Value" "D" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "D_0_1"
        (polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy -1.27 0) (xy 1.27 0))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 1.27 -1.27) (xy -1.27 0) (xy 1.27 1.27) (xy 1.27 -1.27))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
      )
      (symbol "D_1_1"
        (pin passive line (at -2.54 0 0) (length 2.54) (name "K" (effects (font (size 1.27 1.27)))) (number "K" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 0 180) (length 2.54) (name "A" (effects (font (size 1.27 1.27)))) (number "A" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Simulation_SPICE:VDC": """
    (symbol "Simulation_SPICE:VDC" (pin_names (offset 0.254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "V" (at 2.54 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "VDC" (at 2.54 0 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "VDC_0_1"
        (circle (center 0 0) (radius 2.54)
          (stroke (width 0.254) (type default)) (fill (type background))
        )
        (polyline (pts (xy -0.762 1.27) (xy 0.762 1.27))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0 0.762) (xy 0 1.778))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0 -1.778) (xy 0 -0.762))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
      )
      (symbol "VDC_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Simulation_SPICE:IDC": """
    (symbol "Simulation_SPICE:IDC" (pin_names (offset 0.254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "I" (at 2.54 2.54 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "IDC" (at 2.54 0 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "IDC_0_1"
        (circle (center 0 0) (radius 2.54)
          (stroke (width 0.254) (type default)) (fill (type background))
        )
        (polyline (pts (xy 0 -1.778) (xy 0 1.778))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy -0.508 1.016) (xy 0 1.778) (xy 0.508 1.016))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
      )
      (symbol "IDC_1_1"
        (pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 90) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:Q_NPN_BCE": """
    (symbol "Device:Q_NPN_BCE" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "Q" (at 5.08 1.905 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "Q_NPN_BCE" (at 5.08 0 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 5.08 -1.905 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "Q_NPN_BCE_0_1"
        (polyline (pts (xy 0.635 0.635) (xy 2.54 2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.635 -0.635) (xy 2.54 -2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.635 1.905) (xy 0.635 -1.905))
          (stroke (width 0.3048) (type default)) (fill (type none))
        )
        (polyline (pts (xy 1.27 -1.524) (xy 2.286 -2.286) (xy 1.778 -0.762))
          (stroke (width 0) (type default)) (fill (type outline))
        )
      )
      (symbol "Q_NPN_BCE_1_1"
        (pin passive line (at -2.54 0 0) (length 3.175) (name "B" (effects (font (size 1.27 1.27)))) (number "B" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 2.54 270) (length 2.54) (name "C" (effects (font (size 1.27 1.27)))) (number "C" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 -2.54 90) (length 2.54) (name "E" (effects (font (size 1.27 1.27)))) (number "E" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:Q_PNP_BCE": """
    (symbol "Device:Q_PNP_BCE" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "Q" (at 5.08 1.905 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "Q_PNP_BCE" (at 5.08 0 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 5.08 -1.905 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "Q_PNP_BCE_0_1"
        (polyline (pts (xy 0.635 0.635) (xy 2.54 2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.635 -0.635) (xy 2.54 -2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.635 1.905) (xy 0.635 -1.905))
          (stroke (width 0.3048) (type default)) (fill (type none))
        )
        (polyline (pts (xy 2.286 1.524) (xy 1.778 -0.762) (xy 1.27 1.524))
          (stroke (width 0) (type default)) (fill (type outline))
        )
      )
      (symbol "Q_PNP_BCE_1_1"
        (pin passive line (at -2.54 0 0) (length 3.175) (name "B" (effects (font (size 1.27 1.27)))) (number "B" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 2.54 270) (length 2.54) (name "C" (effects (font (size 1.27 1.27)))) (number "C" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 -2.54 90) (length 2.54) (name "E" (effects (font (size 1.27 1.27)))) (number "E" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:Q_NMOS_GDS": """
    (symbol "Device:Q_NMOS_GDS" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "Q" (at 5.08 1.905 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "Q_NMOS_GDS" (at 5.08 0 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 5.08 -1.905 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "Q_NMOS_GDS_0_1"
        (polyline (pts (xy 0.254 0) (xy -2.54 0))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.254 1.905) (xy 0.254 -1.905))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.762 -1.27) (xy 0.762 -2.286))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.762 0.508) (xy 0.762 -0.508))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.762 2.286) (xy 0.762 1.27))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
      )
      (symbol "Q_NMOS_GDS_1_1"
        (pin passive line (at -2.54 0 0) (length 2.794) (name "G" (effects (font (size 1.27 1.27)))) (number "G" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 2.54 270) (length 2.54) (name "D" (effects (font (size 1.27 1.27)))) (number "D" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 -2.54 90) (length 2.54) (name "S" (effects (font (size 1.27 1.27)))) (number "S" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Device:Q_PMOS_GDS": """
    (symbol "Device:Q_PMOS_GDS" (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "Q" (at 5.08 1.905 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "Q_PMOS_GDS" (at 5.08 0 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 5.08 -1.905 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "Q_PMOS_GDS_0_1"
        (polyline (pts (xy 0.254 0) (xy -2.54 0))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.254 1.905) (xy 0.254 -1.905))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.762 -1.27) (xy 0.762 -2.286))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.762 0.508) (xy 0.762 -0.508))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0.762 2.286) (xy 0.762 1.27))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
      )
      (symbol "Q_PMOS_GDS_1_1"
        (pin passive line (at -2.54 0 0) (length 2.794) (name "G" (effects (font (size 1.27 1.27)))) (number "G" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 2.54 270) (length 2.54) (name "D" (effects (font (size 1.27 1.27)))) (number "D" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 -2.54 90) (length 2.54) (name "S" (effects (font (size 1.27 1.27)))) (number "S" (effects (font (size 1.27 1.27)))))
      )
    )""",
    "Simulation_SPICE:SUBCKT": """
    (symbol "Simulation_SPICE:SUBCKT" (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "X" (at 0 1.27 0) (effects (font (size 1.27 1.27))))
      (property "Value" "SUBCKT" (at 0 -1.27 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "SUBCKT_0_1"
        (rectangle (start -5.08 -7.62) (end 5.08 7.62)
          (stroke (width 0.254) (type default)) (fill (type background))
        )
      )
      (symbol "SUBCKT_1_1"
        (pin passive line (at -7.62 5.08 0) (length 2.54) (name "1" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at -7.62 2.54 0) (length 2.54) (name "2" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
        (pin passive line (at -7.62 0 0) (length 2.54) (name "3" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))
        (pin passive line (at -7.62 -2.54 0) (length 2.54) (name "4" (effects (font (size 1.27 1.27)))) (number "4" (effects (font (size 1.27 1.27)))))
        (pin passive line (at -7.62 -5.08 0) (length 2.54) (name "5" (effects (font (size 1.27 1.27)))) (number "5" (effects (font (size 1.27 1.27)))))
      )
    )""",
}


def _build_lib_symbols(used_lib_ids: set[str], include_gnd: bool = False) -> str:
    """Assemble lib_symbols section with only the templates actually used."""
    parts: list[str] = []
    parts.append("  (lib_symbols")
    for lib_id in sorted(used_lib_ids):
        if lib_id in _LIB_SYMBOL_TEMPLATES:
            parts.append(_LIB_SYMBOL_TEMPLATES[lib_id])
    if include_gnd:
        parts.append(_GND_LIB_SYMBOL)
    parts.append("  )")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# S-expression emitters
# ---------------------------------------------------------------------------


def _uid() -> str:
    """Generate a KiCad-compatible UUID."""
    return str(uuid.uuid4())


def _emit_header(sheet_uuid: str) -> str:
    """Emit the file header."""
    return f"""\
(kicad_sch
  (version 20231120)
  (generator "spicebridge")
  (generator_version "0.1")
  (uuid "{sheet_uuid}")
  (paper "A4")
"""


def _emit_symbol_instance(placed: PlacedComponent, sheet_uuid: str) -> str:
    """Emit a single symbol instance."""
    comp = placed.component
    sym = placed.symbol_info
    sym_uuid = _uid()

    # Build pin UUIDs
    pin_lines: list[str] = []
    n_pins = min(len(comp.nodes), len(sym.pin_numbers))
    for i in range(n_pins):
        pin_lines.append(f'      (pin "{sym.pin_numbers[i]}" (uuid "{_uid()}"))')

    # Rotation angle for KiCad (uses degrees)
    angle = placed.rotation

    props = [
        f'      (property "Reference" "{comp.ref}" (at {placed.x + 2.54} {placed.y} 0)'
        f"\n        (effects (font (size 1.27 1.27))))",
        f'      (property "Value" "{comp.value}" (at {placed.x + 2.54} {placed.y + 2.54} 0)'
        f"\n        (effects (font (size 1.27 1.27))))",
        f'      (property "Footprint" "" (at {placed.x} {placed.y} 0)'
        f"\n        (effects (font (size 1.27 1.27)) hide))",
        f'      (property "Datasheet" "~" (at {placed.x} {placed.y} 0)'
        f"\n        (effects (font (size 1.27 1.27)) hide))",
    ]

    lines = [
        f'    (symbol (lib_id "{sym.lib_id}") (at {placed.x} {placed.y} {angle})',
        f'      (uuid "{sym_uuid}")',
    ]
    lines.extend(props)
    # KiCad requires (pin ...) nodes before (instances ...); (pin_names ...)
    # is only valid in lib_symbols definitions, not in placed symbols.
    lines.extend(pin_lines)
    lines.append("      (instances")
    lines.append('        (project ""')
    lines.append(f'          (path "/{sheet_uuid}"')
    lines.append(f'            (reference "{comp.ref}") (unit 1)')
    lines.append("          )")
    lines.append("        )")
    lines.append("      )")
    lines.append("    )")
    return "\n".join(lines)


def _emit_wire(wire: Wire) -> str:
    """Emit a wire segment."""
    x1, y1 = wire.start
    x2, y2 = wire.end
    return (
        f"    (wire (pts (xy {x1} {y1}) (xy {x2} {y2}))\n"
        f"      (stroke (width 0) (type default))\n"
        f'      (uuid "{_uid()}")\n'
        f"    )"
    )


def _emit_power_symbol(x: float, y: float, sheet_uuid: str) -> str:
    """Emit a GND power port symbol whose pin sits exactly at (x, y)."""
    sym_uuid = _uid()
    return (
        f'    (symbol (lib_id "power:GND") (at {x} {y} 0)\n'
        f'      (uuid "{sym_uuid}")\n'
        f'      (property "Reference" "#PWR?" (at {x} {_mm(y + 6.35)} 0)\n'
        f"        (effects (font (size 1.27 1.27)) hide))\n"
        f'      (property "Value" "GND" (at {x} {_mm(y + 3.81)} 0)\n'
        f"        (effects (font (size 1.27 1.27)) hide))\n"
        f'      (property "Footprint" "" (at {x} {y} 0)\n'
        f"        (effects (font (size 1.27 1.27)) hide))\n"
        f'      (property "Datasheet" "" (at {x} {y} 0)\n'
        f"        (effects (font (size 1.27 1.27)) hide))\n"
        f'      (pin "1" (uuid "{_uid()}"))\n'
        f"      (instances\n"
        f'        (project ""\n'
        f'          (path "/{sheet_uuid}"\n'
        f'            (reference "#PWR?") (unit 1)\n'
        f"          )\n"
        f"        )\n"
        f"      )\n"
        f"    )"
    )


def _emit_junction(x: float, y: float) -> str:
    """Emit a junction marker."""
    return (
        f"    (junction (at {x} {y}) (diameter 0) (color 0 0 0 0)\n"
        f'      (uuid "{_uid()}")\n'
        f"    )"
    )


def _emit_net_label(x: float, y: float, name: str) -> str:
    """Emit a net label anchored on the pin at (x, y) so it joins that net."""
    return (
        f'    (label "{name}" (at {x} {y} 0) (fields_autoplaced yes)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f'      (uuid "{_uid()}")\n'
        f"    )"
    )


def _emit_sheet_instances(sheet_uuid: str) -> str:
    """Emit the sheet_instances section."""
    return (
        f'  (sheet_instances\n    (path "/{sheet_uuid}"\n      (page "1")\n    )\n  )'
    )


# ---------------------------------------------------------------------------
# GND lib symbol (always included if ground pins exist)
# ---------------------------------------------------------------------------

_GND_LIB_SYMBOL = """
    (symbol "power:GND" (power) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -6.35 0) (effects (font (size 1.27 1.27)) hide))
      (property "Value" "GND" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "GND_0_1"
        (polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))
          (stroke (width 0) (type default)) (fill (type none))
        )
      )
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 270) (length 0) (name "GND" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_kicad_schematic(
    netlist: str,
    output_dir: Path | None = None,
    filename: str = "circuit.kicad_sch",
) -> tuple[Path, list[str]]:
    """Export a SPICE netlist as a KiCad 8 schematic file.

    Args:
        netlist: SPICE netlist string.
        output_dir: Directory to write output file. Uses cwd if None.
        filename: Output filename.

    Returns:
        Tuple of (output_path, warnings_list).

    Raises:
        ValueError: If the netlist contains no components.
    """
    validate_filename(filename)

    components = parse_netlist(netlist)
    if not components:
        raise ValueError("Netlist contains no components to export")

    warnings: list[str] = []

    # Handle MOSFET 4-pin → 3-pin mapping
    for comp in components:
        if comp.comp_type == "M" and len(comp.nodes) == 4:
            bulk = comp.nodes[3]
            source = comp.nodes[2]
            if bulk.lower() != source.lower():
                warnings.append(
                    f"{comp.ref}: bulk node '{bulk}' differs from source "
                    f"'{source}'; bulk connection dropped in KiCad export"
                )
            comp.nodes = comp.nodes[:3]

    sheet_uuid = _uid()

    # Layout
    placed = _layout_components(components)

    # Wire routing
    wires, junctions, unrouted = _route_nets(placed)

    # Ground symbols
    ground_positions = _find_ground_pins(placed)

    # Net labels
    net_labels = _find_net_labels(placed, unrouted)

    # Collect used lib_ids
    used_lib_ids: set[str] = set()
    for pc in placed:
        used_lib_ids.add(pc.symbol_info.lib_id)

    # Build output
    parts: list[str] = []

    # Header
    parts.append(_emit_header(sheet_uuid))

    # Lib symbols
    parts.append(_build_lib_symbols(used_lib_ids, include_gnd=bool(ground_positions)))
    parts.append("")

    # Symbol instances
    for pc in placed:
        parts.append(_emit_symbol_instance(pc, sheet_uuid))
        parts.append("")

    # Wires
    for wire in wires:
        parts.append(_emit_wire(wire))

    # Junctions
    for jx, jy in junctions:
        parts.append(_emit_junction(jx, jy))

    # Ground power symbols
    for gx, gy in ground_positions:
        parts.append(_emit_power_symbol(gx, gy, sheet_uuid))
        parts.append("")

    # Net labels
    for lx, ly, name in net_labels:
        parts.append(_emit_net_label(lx, ly, name))

    # Sheet instances
    parts.append(_emit_sheet_instances(sheet_uuid))

    # Close
    parts.append(")")

    content = "\n".join(parts) + "\n"

    # Write file
    if output_dir is None:
        output_dir = Path.cwd()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = safe_path(output_dir, filename)
    output_path.write_text(content, encoding="utf-8")

    return output_path, warnings
