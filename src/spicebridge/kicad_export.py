"""Export SPICE netlists to KiCad 8 schematic (.kicad_sch) files."""

from __future__ import annotations

import math
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
    # Distance from the symbol origin to the Reference/Value fields when the
    # fields are placed beside the body
    field_dx: float = 2.54


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
    "V": KiCadSymbolInfo(
        "Simulation_SPICE:VDC", ["1", "2"], [(0, -3.81), (0, 3.81)], 3.81
    ),
    "I": KiCadSymbolInfo(
        "Simulation_SPICE:IDC", ["1", "2"], [(0, -3.81), (0, 3.81)], 3.81
    ),
    "Q_NPN": KiCadSymbolInfo(
        "Device:Q_NPN_BCE",
        ["C", "B", "E"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
        5.08,
    ),
    "Q_PNP": KiCadSymbolInfo(
        "Device:Q_PNP_BCE",
        ["C", "B", "E"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
        5.08,
    ),
    "M_NMOS": KiCadSymbolInfo(
        "Device:Q_NMOS_GDS",
        ["D", "G", "S"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
        5.08,
    ),
    "M_PMOS": KiCadSymbolInfo(
        "Device:Q_PMOS_GDS",
        ["D", "G", "S"],
        [(2.54, -2.54), (-2.54, 0), (2.54, 2.54)],
        5.08,
    ),
    # Controlled sources use the pin layout of KiCad's Simulation_SPICE
    # ESOURCE/GSOURCE (N+ N- C+ C-); F/H/B have only the two output pins.
    "E": KiCadSymbolInfo(
        "Simulation_SPICE:ESOURCE",
        ["1", "2", "3", "4"],
        [(0, -5.08), (0, 5.08), (-5.08, -5.08), (-5.08, 5.08)],
        3.81,
    ),
    "G": KiCadSymbolInfo(
        "Simulation_SPICE:GSOURCE",
        ["1", "2", "3", "4"],
        [(0, -5.08), (0, 5.08), (-5.08, -5.08), (-5.08, 5.08)],
        3.81,
    ),
    "F": KiCadSymbolInfo(
        "Simulation_SPICE:FSOURCE", ["1", "2"], [(0, -5.08), (0, 5.08)], 3.81
    ),
    "H": KiCadSymbolInfo(
        "Simulation_SPICE:HSOURCE", ["1", "2"], [(0, -5.08), (0, 5.08)], 3.81
    ),
    "B": KiCadSymbolInfo(
        "Simulation_SPICE:BSOURCE", ["1", "2"], [(0, -5.08), (0, 5.08)], 3.81
    ),
    # JFETs use the pin layout of KiCad's Simulation_SPICE NJFET/PJFET
    "J_N": KiCadSymbolInfo(
        "Simulation_SPICE:NJFET",
        ["1", "2", "3"],
        [(2.54, -5.08), (-5.08, 0), (2.54, 5.08)],
        5.08,
    ),
    "J_P": KiCadSymbolInfo(
        "Simulation_SPICE:PJFET",
        ["1", "2", "3"],
        [(2.54, -5.08), (-5.08, 0), (2.54, 5.08)],
        5.08,
    ),
}

# Fallback for element types without a symbol: generic 2-pin placeholder
_FALLBACK_SYMBOL = KiCadSymbolInfo("Device:R", ["1", "2"], [(0, -3.81), (0, 3.81)])

# Element types that have no nodes of their own and are not drawn
_SKIPPED_TYPES = {"K": "mutual inductance coupling"}


def _model_types(netlist: str) -> dict[str, str]:
    """Map lowercase .model names to their lowercase device type (npn, pmos...)."""
    types: dict[str, str] = {}
    for line in netlist.splitlines():
        tokens = line.replace("(", " ").split()
        if len(tokens) >= 3 and tokens[0].lower() == ".model":
            types[tokens[1].lower()] = tokens[2].lower()
    return types


def _resolve_symbol_info(
    comp_type: str,
    value: str,
    model_types: dict[str, str] | None = None,
    n_nodes: int = 0,
) -> KiCadSymbolInfo:
    """Return the KiCadSymbolInfo for a SPICE component type and value.

    ``model_types`` (from ``_model_types``) resolves the polarity of Q/M/J
    devices from their .model card; the model name is used as a fallback.
    ``n_nodes`` sizes the generated subcircuit symbol.
    """
    model = value.split()[0].lower() if value.split() else ""
    hint = (model_types or {}).get(model, "") + " " + value.lower()
    if comp_type == "Q":
        if "pnp" in hint:
            return _SYMBOL_MAP["Q_PNP"]
        return _SYMBOL_MAP["Q_NPN"]
    if comp_type == "M":
        if "pmos" in hint:
            return _SYMBOL_MAP["M_PMOS"]
        return _SYMBOL_MAP["M_NMOS"]
    if comp_type == "J":
        if "pjf" in hint:
            return _SYMBOL_MAP["J_P"]
        return _SYMBOL_MAP["J_N"]
    if comp_type == "X":
        n_pins = _subckt_pin_count(n_nodes)
        return KiCadSymbolInfo(
            _subckt_lib_id(n_pins),
            [str(i + 1) for i in range(n_pins)],
            [(-7.62, -y) for y in _subckt_pin_ys(n_pins)],
        )
    if comp_type in _SYMBOL_MAP:
        return _SYMBOL_MAP[comp_type]
    return _FALLBACK_SYMBOL


# ---------------------------------------------------------------------------
# Generated subcircuit symbols
# ---------------------------------------------------------------------------

_SUBCKT_MIN_PINS = 5


def _subckt_pin_count(n_nodes: int) -> int:
    """Number of pins of the subcircuit symbol for an X element."""
    return max(_SUBCKT_MIN_PINS, n_nodes)


def _subckt_lib_id(n_pins: int) -> str:
    """lib_id of the generated subcircuit symbol with ``n_pins`` pins."""
    if n_pins == _SUBCKT_MIN_PINS:
        return "Simulation_SPICE:SUBCKT"
    return f"Simulation_SPICE:SUBCKT_{n_pins}"


def _subckt_pin_ys(n_pins: int) -> list[float]:
    """Pin y positions in lib coordinates (Y up), top to bottom, on grid."""
    top = math.ceil((n_pins - 1) / 2) * _GRID
    return [_mm(top - i * _GRID) for i in range(n_pins)]


def _subckt_symbol(n_pins: int) -> str:
    """Lib symbol for a subcircuit box with ``n_pins`` pins on the left edge."""
    lib_id = _subckt_lib_id(n_pins)
    name = lib_id.split(":", 1)[1]
    ys = _subckt_pin_ys(n_pins)
    top, bottom = _mm(ys[0] + _GRID), _mm(ys[-1] - _GRID)
    font = "(effects (font (size 1.27 1.27)))"
    pins = "\n".join(
        f'        (pin passive line (at -7.62 {y:g} 0) (length 2.54) (name "{i + 1}" '
        f'{font}) (number "{i + 1}" {font}))'
        for i, y in enumerate(ys)
    )
    return f"""
    (symbol "{lib_id}" (pin_numbers hide) (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "X" (at 0 1.27 0) {font})
      (property "Value" "SUBCKT" (at 0 -1.27 0) {font})
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "{name}_0_1"
        (rectangle (start -5.08 {bottom:g}) (end 5.08 {top:g})
          (stroke (width 0.254) (type default)) (fill (type background))
        )
      )
      (symbol "{name}_1_1"
{pins}
      )
    )"""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_COL_SOURCES = 50.8
_COL_SERIES = 101.6
_COL_SHUNT = 152.4
_Y_START = 50.8
_Y_GAP = 5.08  # free space between stacked components
_SOURCE_TYPES = ("V", "I", "E", "G", "F", "H", "B")


def _snap_to_grid(val: float) -> float:
    """Round a value to the nearest KiCad grid point."""
    return _mm(round(val / _GRID) * _GRID)


def _mm(val: float) -> float:
    """Round a coordinate to 1 µm to strip floating-point noise."""
    return round(val, 4) + 0.0


def _rotate(dx: float, dy: float, rotation: float) -> tuple[float, float]:
    """Rotate a schematic offset (Y down) the way KiCad rotates symbols.

    KiCad rotates counter-clockwise on screen, which in Y-down coordinates is
    (x, y) -> (x*cos + y*sin, -x*sin + y*cos).
    """
    angle_rad = math.radians(rotation)
    cos_a = round(math.cos(angle_rad), 9)
    sin_a = round(math.sin(angle_rad), 9)
    return dx * cos_a + dy * sin_a, -dx * sin_a + dy * cos_a


def _choose_rotation(sym: KiCadSymbolInfo, horizontal: bool) -> int:
    """Pick a rotation so a 2-pin part runs pin 1 -> pin 2 left-to-right
    (``horizontal``) or top-to-bottom. Multi-pin parts are never rotated."""
    if len(sym.pin_offsets) != 2:
        return 0

    def sign(v: float) -> int:
        return 0 if abs(v) < 1e-6 else (1 if v > 0 else -1)

    (x1, y1), (x2, y2) = sym.pin_offsets
    target = (1, 0) if horizontal else (0, 1)
    for rotation in (0, 90, 180, 270):
        dx, dy = _rotate(x2 - x1, y2 - y1, rotation)
        if (sign(dx), sign(dy)) == target:
            return rotation
    return 0


def _vertical_extent(
    sym: KiCadSymbolInfo, rotation: float, n_pins: int
) -> tuple[float, float]:
    """Space needed above and below the symbol origin, including fields."""
    ys = [_rotate(dx, dy, rotation)[1] for dx, dy in sym.pin_offsets[:n_pins]]
    above = max([-y for y in ys] + [0.0]) + _GRID
    below = max([y for y in ys] + [0.0]) + _GRID
    # Room for the Reference/Value fields
    return max(above, 3.81), max(below, 3.81)


def _layout_components(
    components: list[ParsedComponent],
    model_types: dict[str, str] | None = None,
) -> list[PlacedComponent]:
    """Assign positions to components using column-based layout.

    Sources go to the first column, parts connected to ground to the third,
    everything else to the middle column (2-pin parts drawn horizontally).
    Components are stacked by their actual height so they never overlap.
    """
    sources: list[ParsedComponent] = []
    series: list[ParsedComponent] = []
    shunt: list[ParsedComponent] = []

    for comp in components:
        if comp.comp_type in _SOURCE_TYPES:
            sources.append(comp)
        elif any(_is_ground(n) for n in comp.nodes):
            shunt.append(comp)
        else:
            series.append(comp)

    placed: list[PlacedComponent] = []
    columns = (
        (sources, _COL_SOURCES, False),
        (series, _COL_SERIES, True),
        (shunt, _COL_SHUNT, False),
    )
    for column, x, horizontal in columns:
        cursor = _Y_START
        for comp in column:
            sym = _resolve_symbol_info(
                comp.comp_type, comp.value, model_types, len(comp.nodes)
            )
            rotation = _choose_rotation(sym, horizontal)
            above, below = _vertical_extent(sym, rotation, len(comp.nodes))
            y = _snap_to_grid(math.ceil((cursor + above) / _GRID) * _GRID)
            placed.append(PlacedComponent(comp, x, y, rotation, sym))
            cursor = y + below + _Y_GAP

    return placed


# ---------------------------------------------------------------------------
# Pin positions
# ---------------------------------------------------------------------------


def _pin_positions(
    placed: PlacedComponent,
) -> list[tuple[float, float]]:
    """Compute absolute pin positions for a placed component.

    Pin offsets are in schematic coordinates (Y down). Positions are not
    snapped to the grid: many pins sit on 1.27 mm half-grid points and wires
    must end exactly on them to connect.
    """
    positions: list[tuple[float, float]] = []
    n_pins = min(len(placed.component.nodes), len(placed.symbol_info.pin_offsets))
    for i in range(n_pins):
        dx, dy = placed.symbol_info.pin_offsets[i]
        rx, ry = _rotate(dx, dy, placed.rotation)
        positions.append((_mm(placed.x + rx), _mm(placed.y + ry)))

    return positions


def _pin_directions(placed: PlacedComponent) -> list[int]:
    """Direction each pin points away from the body, as a text angle.

    0 = right, 90 = up, 180 = left, 270 = down. Derived from the pin offset:
    its dominant axis (vertical on a tie, e.g. a transistor's collector).
    """
    directions: list[int] = []
    n_pins = min(len(placed.component.nodes), len(placed.symbol_info.pin_offsets))
    for dx, dy in placed.symbol_info.pin_offsets[:n_pins]:
        if abs(dy) >= abs(dx):
            ux, uy = 0.0, math.copysign(1, dy)
        else:
            ux, uy = math.copysign(1, dx), 0.0
        rx, ry = _rotate(ux, uy, placed.rotation)
        if abs(rx) > abs(ry):
            directions.append(0 if rx > 0 else 180)
        else:
            directions.append(270 if ry > 0 else 90)
    return directions


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


def _body_box(placed: PlacedComponent) -> tuple[float, float, float, float]:
    """Approximate body of a placed component: its pins plus the origin area.

    Returns (x_min, y_min, x_max, y_max).
    """
    pins = _pin_positions(placed)
    xs = [p[0] for p in pins] + [placed.x - 1.27, placed.x + 1.27]
    ys = [p[1] for p in pins] + [placed.y - 1.27, placed.y + 1.27]
    return min(xs), min(ys), max(xs), max(ys)


def _crosses_box(
    wire: Wire, box: tuple[float, float, float, float], eps: float = 1e-6
) -> bool:
    """Return True if the axis-aligned wire runs through the box interior."""
    (x1, y1), (x2, y2) = wire.start, wire.end
    bx1, by1, bx2, by2 = box
    lo_x, hi_x = max(min(x1, x2), bx1), min(max(x1, x2), bx2)
    lo_y, hi_y = max(min(y1, y2), by1), min(max(y1, y2), by2)
    if y1 == y2:
        return by1 + eps < y1 < by2 - eps and lo_x < hi_x - eps
    return bx1 + eps < x1 < bx2 - eps and lo_y < hi_y - eps


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
    bodies = [_body_box(pc) for pc in placed_components]

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
        conflict = (
            any(_on_segment(p, w) for w in net_wires for p in foreign_points)
            or any(
                _on_segment(pt, fw)
                for w in net_wires
                for pt in (w.start, w.end)
                for fw in foreign_wires
            )
            # Wires drawn across a component body are unreadable
            or any(_crosses_box(w, box) for w in net_wires for box in bodies)
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
) -> list[tuple[float, float, int]]:
    """Find pins connected to ground for placing GND power symbols.

    Returns (x, y, direction) with the pin direction from ``_pin_directions``
    so the GND symbol can point away from the component.
    """
    ground_positions: list[tuple[float, float, int]] = []
    for pc in placed_components:
        pins = _pin_positions(pc)
        directions = _pin_directions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins) and _is_ground(node):
                ground_positions.append((*pins[idx], directions[idx]))
    return ground_positions


# ---------------------------------------------------------------------------
# Net labels
# ---------------------------------------------------------------------------


def _find_net_labels(
    placed_components: list[PlacedComponent],
    unrouted: set[str] | None = None,
) -> list[tuple[float, float, str, int]]:
    """Find named nets (not numbered, not ground) and return label positions.

    Nets in ``unrouted`` have no wires, so every one of their pins is labelled
    (numbered nets included) to connect them by name. Each label carries the
    direction of its pin so the text points away from the component.
    """
    unrouted = unrouted or set()
    net_pins = _net_pins(placed_components)
    pin_dirs: dict[tuple[float, float], int] = {}
    for pc in placed_components:
        pin_dirs.update(zip(_pin_positions(pc), _pin_directions(pc), strict=True))

    labels: list[tuple[float, float, str, int]] = []
    for net_name, pin_list in net_pins.items():
        if net_name in unrouted:
            labels.extend((x, y, net_name, pin_dirs[(x, y)]) for x, y in pin_list)
            continue
        # Skip purely numeric net names
        if net_name.isdigit():
            continue
        if pin_list:
            x, y = pin_list[0]
            labels.append((x, y, net_name, pin_dirs[(x, y)]))

    return labels


# ---------------------------------------------------------------------------
# Lib symbol templates
# ---------------------------------------------------------------------------

_FONT = "(effects (font (size 1.27 1.27)))"


def _polyline(points: list[tuple[float, float]], fill: str = "none") -> str:
    """Lib symbol polyline (lib coordinates, Y up)."""
    pts = " ".join(f"(xy {x} {y})" for x, y in points)
    return (
        f"        (polyline (pts {pts})\n"
        f"          (stroke (width 0.254) (type default)) (fill (type {fill}))\n"
        f"        )"
    )


def _pin(number: str, x: float, y: float, angle: int, etype: str = "passive") -> str:
    """Lib symbol pin with a 2.54 mm lead and hidden name."""
    return (
        f"        (pin {etype} line (at {x} {y} {angle}) (length 2.54) "
        f'(name "~" {_FONT}) (number "{number}" {_FONT}))'
    )


def _lib_symbol(
    lib_id: str, ref: str, field_x: float, graphics: list[str], pins: list[str]
) -> str:
    """Assemble a lib symbol with Reference/Value fields right of the body."""
    name = lib_id.split(":", 1)[1]
    body = "\n".join(graphics)
    pin_lines = "\n".join(pins)
    return f"""
    (symbol "{lib_id}" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "{ref}" (at {field_x} 1.27 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Value" "{name}" (at {field_x} -1.27 0) (effects (font (size 1.27 1.27)) (justify left)))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "{name}_0_1"
{body}
      )
      (symbol "{name}_1_1"
{pin_lines}
      )
    )"""


_DIAMOND = [(0, 2.54), (2.54, 0), (0, -2.54), (-2.54, 0), (0, 2.54)]
_PLUS_MINUS = [
    _polyline([(-0.635, 1.016), (0.635, 1.016)]),
    _polyline([(0, 0.381), (0, 1.651)]),
    _polyline([(-0.635, -1.016), (0.635, -1.016)]),
]
_ARROW_DOWN = [
    _polyline([(0, 1.524), (0, -1.524)]),
    _polyline([(-0.508, -0.762), (0, -1.524), (0.508, -0.762)]),
]
_CONTROL_MARKS = [
    _polyline([(-4.318, 1.905), (-3.302, 1.905)]),
    _polyline([(-3.81, 1.397), (-3.81, 2.413)]),
    _polyline([(-4.318, -1.905), (-3.302, -1.905)]),
]


def _source_symbol(lib_id: str, ref: str, marks: list[str], control: bool) -> str:
    """Diamond-shaped controlled/behavioral source.

    Output pins N+ (1, top) and N- (2, bottom); with ``control`` also C+ (3)
    and C- (4) on the left, matching KiCad's ESOURCE/GSOURCE pin layout.
    """
    graphics = [_polyline(_DIAMOND, "background"), *marks]
    pins = [_pin("1", 0, 5.08, 270), _pin("2", 0, -5.08, 90)]
    if control:
        graphics += _CONTROL_MARKS
        pins += [
            _pin("3", -5.08, 5.08, 270, "input"),
            _pin("4", -5.08, -5.08, 90, "input"),
        ]
    return _lib_symbol(lib_id, ref, 3.81, graphics, pins)


def _jfet_symbol(lib_id: str, p_channel: bool) -> str:
    """JFET with D (1, top right), G (2, left), S (3, bottom right)."""
    arrow = (
        [(0, 0.508), (-0.635, 0), (0, -0.508), (0, 0.508)]
        if p_channel
        else [(-0.635, 0.508), (0, 0), (-0.635, -0.508), (-0.635, 0.508)]
    )
    graphics = [
        _polyline([(0.635, 2.54), (0.635, -2.54)]),
        _polyline([(0.635, 1.905), (2.54, 1.905), (2.54, 2.54)]),
        _polyline([(0.635, -1.905), (2.54, -1.905), (2.54, -2.54)]),
        _polyline([(-2.54, 0), (0.635, 0)]),
        _polyline(arrow, "outline"),
    ]
    pins = [
        _pin("1", 2.54, 5.08, 270),
        _pin("2", -5.08, 0, 0, "input"),
        _pin("3", 2.54, -5.08, 90),
    ]
    return _lib_symbol(lib_id, "J", 5.08, graphics, pins)


_LIB_SYMBOL_TEMPLATES: dict[str, str] = {
    "Simulation_SPICE:ESOURCE": _source_symbol(
        "Simulation_SPICE:ESOURCE", "E", _PLUS_MINUS, control=True
    ),
    "Simulation_SPICE:GSOURCE": _source_symbol(
        "Simulation_SPICE:GSOURCE", "G", _ARROW_DOWN, control=True
    ),
    "Simulation_SPICE:HSOURCE": _source_symbol(
        "Simulation_SPICE:HSOURCE", "H", _PLUS_MINUS, control=False
    ),
    "Simulation_SPICE:FSOURCE": _source_symbol(
        "Simulation_SPICE:FSOURCE", "F", _ARROW_DOWN, control=False
    ),
    "Simulation_SPICE:BSOURCE": _source_symbol(
        "Simulation_SPICE:BSOURCE", "B", [], control=False
    ),
    "Simulation_SPICE:NJFET": _jfet_symbol("Simulation_SPICE:NJFET", False),
    "Simulation_SPICE:PJFET": _jfet_symbol("Simulation_SPICE:PJFET", True),
    "Device:R": """
    (symbol "Device:R" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:C" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:L" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:D" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Simulation_SPICE:VDC" (pin_numbers hide) (pin_names (offset 0.254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Simulation_SPICE:IDC" (pin_numbers hide) (pin_names (offset 0.254)) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:Q_NPN_BCE" (pin_numbers hide) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:Q_PNP_BCE" (pin_numbers hide) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:Q_NMOS_GDS" (pin_numbers hide) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
    (symbol "Device:Q_PMOS_GDS" (pin_numbers hide) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
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
}


def _lib_symbol_text(lib_id: str) -> str | None:
    """Return the lib symbol definition for a lib_id (subcircuits generated)."""
    if lib_id in _LIB_SYMBOL_TEMPLATES:
        return _LIB_SYMBOL_TEMPLATES[lib_id]
    if lib_id == _subckt_lib_id(_SUBCKT_MIN_PINS):
        return _subckt_symbol(_SUBCKT_MIN_PINS)
    prefix = "Simulation_SPICE:SUBCKT_"
    if lib_id.startswith(prefix) and lib_id[len(prefix) :].isdigit():
        return _subckt_symbol(int(lib_id[len(prefix) :]))
    return None


def _build_lib_symbols(used_lib_ids: set[str], include_gnd: bool = False) -> str:
    """Assemble lib_symbols section with only the templates actually used."""
    parts: list[str] = []
    parts.append("  (lib_symbols")
    for lib_id in sorted(used_lib_ids):
        text = _lib_symbol_text(lib_id)
        if text is not None:
            parts.append(text)
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


def _field_positions(
    placed: PlacedComponent,
) -> tuple[tuple[float, float], tuple[float, float], str]:
    """Positions of the Reference and Value fields, plus their justification.

    Subcircuits get both fields centred inside the box, horizontal 2-pin parts
    above and below the body, everything else to the right of the body.
    """
    x, y = placed.x, placed.y
    if placed.component.comp_type == "X":
        return (x, _mm(y - 1.27)), (x, _mm(y + 1.27)), ""
    pins = _pin_positions(placed)
    if len(pins) == 2 and pins[0][1] == pins[1][1]:
        return (x, _mm(y - 2.54)), (x, _mm(y + 2.54)), ""
    fx = _mm(x + placed.symbol_info.field_dx)
    return (fx, _mm(y - 1.27)), (fx, _mm(y + 1.27)), "left"


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

    (rx, ry), (vx, vy), justify = _field_positions(placed)
    effects = "(effects (font (size 1.27 1.27))" + (
        f" (justify {justify}))" if justify else ")"
    )
    # KiCad adds the symbol rotation to the field angle; compensate so the
    # text of rotated parts stays horizontal
    fa = 90 if angle in (90, 270) else 0
    props = [
        f'      (property "Reference" "{comp.ref}" (at {rx} {ry} {fa})'
        f"\n        {effects})",
        f'      (property "Value" "{comp.value}" (at {vx} {vy} {fa})'
        f"\n        {effects})",
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


def _emit_power_symbol(
    x: float, y: float, sheet_uuid: str, direction: int = 270
) -> str:
    """Emit a GND power port symbol whose pin sits exactly at (x, y).

    ``direction`` is the pin direction (see ``_pin_directions``); the symbol
    is rotated to point the same way, i.e. away from the component.
    """
    sym_uuid = _uid()
    rotation = (direction + 90) % 360
    return (
        f'    (symbol (lib_id "power:GND") (at {x} {y} {rotation})\n'
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


def _emit_net_label(x: float, y: float, name: str, direction: int = 0) -> str:
    """Emit a net label anchored on the pin at (x, y) so it joins that net.

    Labels on left/right pins run away from the component (KiCad stores
    left-running labels with angle 180 and right justification). Labels on
    up/down pins stay horizontal and sit above/below the pin end.
    """
    if direction == 180:
        angle, justify = 180, "right bottom"
    elif direction == 0:
        angle, justify = 0, "left bottom"
    else:
        angle, justify = 0, "left bottom" if direction == 90 else "left top"
    return (
        f'    (label "{name}" (at {x} {y} {angle}) (fields_autoplaced yes)\n'
        f"      (effects (font (size 1.27 1.27)) (justify {justify}))\n"
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
    (symbol "power:GND" (power) (pin_numbers hide) (pin_names (offset 0) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)
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

    # Elements that are not drawn, and types without a dedicated symbol
    drawn: list[ParsedComponent] = []
    for comp in components:
        if comp.comp_type in _SKIPPED_TYPES:
            warnings.append(
                f"{comp.ref}: {_SKIPPED_TYPES[comp.comp_type]} is not shown "
                f"in KiCad export"
            )
            continue
        if comp.comp_type not in _SYMBOL_MAP and comp.comp_type not in (
            "Q",
            "M",
            "J",
            "X",
        ):
            warnings.append(
                f"{comp.ref}: no KiCad symbol for '{comp.comp_type}' elements; "
                f"exported as a 2-pin placeholder (only the first two nodes "
                f"are connected)"
            )
        drawn.append(comp)
    components = drawn

    sheet_uuid = _uid()

    # Layout
    placed = _layout_components(components, _model_types(netlist))

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
    for gx, gy, direction in ground_positions:
        parts.append(_emit_power_symbol(gx, gy, sheet_uuid, direction))
        parts.append("")

    # Net labels
    for lx, ly, name, direction in net_labels:
        parts.append(_emit_net_label(lx, ly, name, direction))

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
