"""Export SPICE netlists to KiCad 8 schematic (.kicad_sch) files."""

from __future__ import annotations

import heapq
import math
import re
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from spicebridge.sanitize import safe_path, validate_filename
from spicebridge.schematic import (
    ParsedComponent,
    _is_ground,
    _split_params,
    parse_netlist,
)

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
    # Half size of the symbol graphics around its origin (e.g. a source's
    # circle or diamond), used to keep wires off the body
    body_half: float = 1.27
    # Pin names of generated subcircuit symbols (the .subckt ports)
    pin_names: list[str] | None = None


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
    net: str = ""


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
        "Simulation_SPICE:VDC", ["1", "2"], [(0, -3.81), (0, 3.81)], 3.81, 2.54
    ),
    "I": KiCadSymbolInfo(
        "Simulation_SPICE:IDC", ["1", "2"], [(0, -3.81), (0, 3.81)], 3.81, 2.54
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
    # Controlled sources use the pin numbering of KiCad's Simulation_SPICE
    # ESOURCE/GSOURCE (N+ N- C+ C-); the control pins point left like op-amp
    # inputs. F/H/B have only the two output pins.
    "E": KiCadSymbolInfo(
        "Simulation_SPICE:ESOURCE",
        ["1", "2", "3", "4"],
        [(0, -5.08), (0, 5.08), (-7.62, -2.54), (-7.62, 2.54)],
        3.81,
        2.54,
    ),
    "G": KiCadSymbolInfo(
        "Simulation_SPICE:GSOURCE",
        ["1", "2", "3", "4"],
        [(0, -5.08), (0, 5.08), (-7.62, -2.54), (-7.62, 2.54)],
        3.81,
        2.54,
    ),
    "F": KiCadSymbolInfo(
        "Simulation_SPICE:FSOURCE", ["1", "2"], [(0, -5.08), (0, 5.08)], 3.81, 2.54
    ),
    "H": KiCadSymbolInfo(
        "Simulation_SPICE:HSOURCE", ["1", "2"], [(0, -5.08), (0, 5.08)], 3.81, 2.54
    ),
    "B": KiCadSymbolInfo(
        "Simulation_SPICE:BSOURCE", ["1", "2"], [(0, -5.08), (0, 5.08)], 3.81, 2.54
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
    subckt_ports: dict[str, list[str]] | None = None,
) -> KiCadSymbolInfo:
    """Return the KiCadSymbolInfo for a SPICE component type and value.

    ``model_types`` (from ``_model_types``) resolves the polarity of Q/M/J
    devices from their .model card; the model name is used as a fallback.
    ``n_nodes`` sizes the generated subcircuit symbol; with ``subckt_ports``
    (from ``_subckt_ports``) its pins are named after the .subckt ports.
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
        ports = (subckt_ports or {}).get(model)
        if ports and len(ports) == n_nodes:
            return KiCadSymbolInfo(
                _subckt_named_lib_id(model),
                [str(i + 1) for i in range(n_nodes)],
                [(-7.62, -y) for y in _subckt_pin_ys(n_nodes)],
                pin_names=ports,
            )
        n_pins = _subckt_pin_count(n_nodes)
        return KiCadSymbolInfo(
            _subckt_lib_id(n_pins),
            [str(i + 1) for i in range(n_pins)],
            [(-7.62, -y) for y in _subckt_pin_ys(n_pins)],
        )
    if comp_type in ("E", "G") and n_nodes == 2:
        # Behavioral form (VALUE=, POLY, TABLE...): only output pins
        return _SYMBOL_MAP["B"]
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


def _subckt_named_lib_id(subckt_name: str) -> str:
    """lib_id of the generated symbol for a subcircuit with named ports."""
    return "Subcircuit:" + re.sub(r"[^A-Za-z0-9_.-]", "_", subckt_name)


def _subckt_ports(netlist: str) -> dict[str, list[str]]:
    """Map lowercase .subckt names to their port names (parameters dropped)."""
    lines: list[str] = []
    for line in netlist.splitlines():
        stripped = line.strip()
        if stripped.startswith("+") and lines:
            lines[-1] += " " + stripped[1:]
        else:
            lines.append(stripped)
    ports: dict[str, list[str]] = {}
    for line in lines:
        tokens = line.split()
        if len(tokens) >= 2 and tokens[0].lower() == ".subckt":
            ports[tokens[1].lower()] = _split_params(tokens[2:])[0]
    return ports


def _subckt_pin_ys(n_pins: int) -> list[float]:
    """Pin y positions in lib coordinates (Y up), top to bottom, on grid."""
    top = math.ceil((n_pins - 1) / 2) * _GRID
    return [_mm(top - i * _GRID) for i in range(n_pins)]


def _subckt_box(
    n_pins: int, pin_names: list[str] | None = None
) -> tuple[float, float, float]:
    """Right, top and bottom edge of the subcircuit box in lib coordinates.

    The left edge is always at -5.08; the box grows to the right so the
    longest pin name fits (about 1.27 mm per character at 1.27 mm font size).
    """
    ys = _subckt_pin_ys(n_pins)
    longest = max((len(n) for n in pin_names), default=0) if pin_names else 0
    width = max(10.16, math.ceil((longest * 1.27 + 2.54) / _GRID) * _GRID)
    return _mm(-5.08 + width), _mm(ys[0] + _GRID), _mm(ys[-1] - _GRID)


def _subckt_symbol(
    n_pins: int, lib_id: str | None = None, pin_names: list[str] | None = None
) -> str:
    """Lib symbol for a subcircuit box with ``n_pins`` pins on the left edge.

    Pins are named after ``pin_names`` (the .subckt ports) when given, else
    numbered. Reference and Value sit above and below the box.
    """
    lib_id = lib_id or _subckt_lib_id(n_pins)
    name = lib_id.split(":", 1)[1]
    ys = _subckt_pin_ys(n_pins)
    right, top, bottom = _subckt_box(n_pins, pin_names)
    center = _mm((right - 5.08) / 2)
    names = pin_names or [str(i + 1) for i in range(n_pins)]
    font = "(effects (font (size 1.27 1.27)))"
    pins = "\n".join(
        f"        (pin passive line (at -7.62 {y:g} 0) (length 2.54) "
        f'(name "{_escape(pin_name)}" {font}) (number "{i + 1}" {font}))'
        for i, (y, pin_name) in enumerate(zip(ys, names, strict=True))
    )
    return f"""
    (symbol "{lib_id}" (pin_numbers hide) (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "X" (at {center:g} {_mm(top + 1.27):g} 0) {font})
      (property "Value" "SUBCKT" (at {center:g} {_mm(bottom - 1.27):g} 0) {font})
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "{name}_0_1"
        (rectangle (start -5.08 {bottom:g}) (end {right:g} {top:g})
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

_ORIGIN = (25.4, 30.48)  # top-left corner of the drawing on the sheet
_MIN_PITCH = 25.4  # minimum distance between two net columns
_ROW_STEP = 10.16  # distance between the main row and the rows above it
_DROP = 7.62  # main row -> top pin of the parts hanging below a column
_ITEM_GAP = 5.08  # horizontal gap between parts hanging below one column

# Multi-pin parts: SPICE node indices of their inputs and outputs, used to
# order nets left to right (e.g. base -> collector/emitter)
_SIGNAL_FLOW: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {
    "E": ((2, 3), (0, 1)),
    "G": ((2, 3), (0, 1)),
    "Q": ((1,), (0, 2)),
    "M": ((1,), (0, 2)),
    "J": ((1,), (0, 2)),
}


def _snap_to_grid(val: float) -> float:
    """Round a value to the nearest KiCad grid point."""
    return _mm(round(val / _GRID) * _GRID)


def _ceil_to_grid(val: float) -> float:
    """Round a value up to the next KiCad grid point (ignoring float noise)."""
    return _mm(math.ceil(val / _GRID - 1e-6) * _GRID)


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


_SIGNAL_SOURCE_RE = re.compile(r"\b(ac|sin|pulse|pwl|exp|sffm|am)\b", re.IGNORECASE)


def _supply_nets(components: list[ParsedComponent]) -> set[str]:
    """Nets driven by a voltage source without AC or transient signal."""
    return {
        node
        for comp in components
        if comp.comp_type == "V" and not _SIGNAL_SOURCE_RE.search(comp.value)
        for node in comp.nodes[:2]
        if not _is_ground(node)
    }


def _used_nodes(comp: ParsedComponent, sym: KiCadSymbolInfo) -> list[str]:
    """Nodes that are actually drawn (one per symbol pin)."""
    return comp.nodes[: len(sym.pin_offsets)]


def _net_levels(
    components: list[ParsedComponent], symbols: list[KiCadSymbolInfo]
) -> dict[str, int]:
    """Assign every non-ground net a column index following the signal flow.

    Nets of independent sources start in column 0. Every other net gets the
    breadth-first distance from there over the components, then multi-pin
    parts push their outputs right of their inputs (``_SIGNAL_FLOW``), e.g.
    an op-amp output lands right of its input even if a feedback capacitor
    connects it to an earlier net.
    """
    adjacency: dict[str, list[str]] = {}
    for comp, sym in zip(components, symbols, strict=True):
        nets = [n for n in _used_nodes(comp, sym) if not _is_ground(n)]
        for a in nets:
            adjacency.setdefault(a, [])
            adjacency[a] += [b for b in nets if b != a and b not in adjacency[a]]

    fixed = [
        n
        for comp in components
        if comp.comp_type in ("V", "I")
        for n in comp.nodes[:2]
        if not _is_ground(n)
    ]
    supplies = _supply_nets(components)
    levels: dict[str, int] = {}

    def spread(starts: list[str], base: int, stop: set[str]) -> None:
        queue = deque()
        for net in starts:
            if net not in levels:
                levels[net] = base
                queue.append(net)
        while queue:
            net = queue.popleft()
            if net in stop:
                continue
            for other in adjacency[net]:
                if other not in levels:
                    levels[other] = levels[net] + 1
                    queue.append(other)

    # Signal sources first, without walking through supply rails (a supply
    # tied to every stage would otherwise pull all stages into one column)
    for net in supplies:
        levels[net] = 0
    spread([n for n in fixed if n not in supplies], 0, supplies)
    for net in supplies:
        del levels[net]
    spread(fixed, 0, set())
    for net in adjacency:
        if net not in levels:
            spread([net], max(levels.values(), default=-1) + 1, set())

    fixed_set = set(fixed)
    for _ in range(len(levels)):
        changed = False
        for comp, sym in zip(components, symbols, strict=True):
            flow = _SIGNAL_FLOW.get(comp.comp_type)
            nodes = _used_nodes(comp, sym)
            if not flow or len(nodes) <= max(flow[0] + flow[1]):
                continue
            inputs = [levels[nodes[i]] for i in flow[0] if not _is_ground(nodes[i])]
            if not inputs:
                continue
            for i in flow[1]:
                net = nodes[i]
                if _is_ground(net) or net in fixed_set:
                    continue
                if levels[net] <= max(inputs):
                    levels[net] = max(inputs) + 1
                    changed = True
        if not changed:
            break

    # Close gaps so every column index is used
    rank = {lvl: i for i, lvl in enumerate(sorted(set(levels.values())))}
    return {net: rank[lvl] for net, lvl in levels.items()}


def _local_box(
    comp: ParsedComponent, sym: KiCadSymbolInfo, rotation: int
) -> tuple[float, float, float, float]:
    """Body box of a component placed at the origin."""
    return _body_box(PlacedComponent(comp, 0.0, 0.0, rotation, sym))


def _layout_components(
    components: list[ParsedComponent],
    model_types: dict[str, str] | None = None,
    subckt_ports: dict[str, list[str]] | None = None,
) -> list[PlacedComponent]:
    """Place components left to right following the signal flow.

    Every net gets a column (``_net_levels``). 2-pin parts between two nets
    are drawn horizontally on the main row between their columns, or in a
    row above it when they span several columns (feedback) or run parallel
    to another part. Parts tied to ground, and multi-pin parts, hang below
    the column of their net. The drawing is moved to the top-left corner of
    the sheet at the end.
    """
    symbols = [
        _resolve_symbol_info(
            c.comp_type, c.value, model_types, len(c.nodes), subckt_ports
        )
        for c in components
    ]
    levels = _net_levels(components, symbols)
    n_columns = max(levels.values(), default=0) + 1

    supplies = _supply_nets(components)
    series: list[tuple[int, int, int, int]] = []  # (index, rotation, lo, hi)
    # column -> (index, rotation, pin aligned with the column)
    hanging: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    pullups: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for idx, (comp, sym) in enumerate(zip(components, symbols, strict=True)):
        nodes = _used_nodes(comp, sym)
        nets = [n for n in nodes if not _is_ground(n)]
        if len(sym.pin_offsets) == 2 and len(nodes) == 2:
            if len(nets) == 2 and (nets[0] in supplies) != (nets[1] in supplies):
                # Between a net and a supply rail: stands above the net with
                # the supply on top (pull-up, collector resistor, ...)
                rotation, bottom = _choose_rotation(sym, horizontal=False), 1
                if nets[1] in supplies:
                    rotation, bottom = (rotation + 180) % 360, 0
                pullups[levels[nets[bottom]]].append((idx, rotation, bottom))
                continue
            if len(nets) == 2 and nets[0] != nets[1]:
                rotation = _choose_rotation(sym, horizontal=True)
                if levels[nets[0]] > levels[nets[1]]:
                    rotation = (rotation + 180) % 360
                lo, hi = sorted((levels[nets[0]], levels[nets[1]]))
                series.append((idx, rotation, lo, hi))
                continue
            # Tied to ground: the net side on top
            rotation = _choose_rotation(sym, horizontal=False)
            top = 0
            if nets and _is_ground(nodes[0]):
                rotation, top = (rotation + 180) % 360, 1
            column = levels[nets[0]] if nets else n_columns
            hanging[column].append((idx, rotation, top))
            continue
        # Multi-pin part: align the output pin with the highest column
        flow = _SIGNAL_FLOW.get(comp.comp_type)
        candidates = list(flow[1]) if flow and len(nodes) > max(sum(flow, ())) else []
        candidates = [i for i in candidates if not _is_ground(nodes[i])] or [
            i for i, n in enumerate(nodes) if not _is_ground(n)
        ]
        if candidates:
            align = max(candidates, key=lambda i: levels[nodes[i]])
            hanging[levels[nodes[align]]].append((idx, 0, align))
        else:
            hanging[n_columns].append((idx, 0, 0))

    def pin_rel(idx: int, rotation: int, pin: int) -> tuple[float, float]:
        return _rotate(*symbols[idx].pin_offsets[pin], rotation)

    # Column positions: wide enough for the parts hanging below each column
    # and for the horizontal parts between two neighbouring columns
    gap_width: dict[int, float] = defaultdict(float)
    for idx, rotation, lo, hi in series:
        if hi == lo + 1:
            bx1, _, bx2, _ = _local_box(components[idx], symbols[idx], rotation)
            gap_width[lo] = max(gap_width[lo], bx2 - bx1)

    placed_at: dict[int, PlacedComponent] = {}
    pullup_x: dict[int, float] = {}
    column_x: list[float] = []
    right_edge = -math.inf
    for column in range(n_columns + 1):
        groups = [hanging.get(column, []), pullups.get(column, [])]
        x = column_x[-1] + _MIN_PITCH if column_x else 0.0
        if column_x:
            x = max(x, column_x[-1] + gap_width[column - 1] + 4 * _GRID)
        for items in groups:
            if items:
                idx, rotation, pin = items[0]
                bx1 = _local_box(components[idx], symbols[idx], rotation)[0]
                px = pin_rel(idx, rotation, pin)[0]
                x = max(x, right_edge + _ITEM_GAP - (bx1 - px))
        x = _ceil_to_grid(x)
        column_x.append(x)
        for group, items in enumerate(groups):
            # Side by side, the first one with its pin on the column
            cursor = None
            for idx, rotation, pin in items:
                comp, sym = components[idx], symbols[idx]
                bx1, by1, bx2, _ = _local_box(comp, sym, rotation)
                px, _ = pin_rel(idx, rotation, pin)
                ox = _ceil_to_grid(x - px if cursor is None else cursor - bx1)
                cursor = ox + bx2 + _ITEM_GAP
                right_edge = max(right_edge, ox + bx2)
                if group == 1:
                    pullup_x[idx] = ox  # y follows once the rows are known
                    continue
                n_pins = len(sym.pin_offsets)
                top = min(pin_rel(idx, rotation, i)[1] for i in range(n_pins))
                # Top pin _DROP below the main row, text clear of the main row's
                oy = _ceil_to_grid(max(_DROP - top, 2 * _GRID - by1))
                placed_at[idx] = PlacedComponent(comp, ox, oy, rotation, sym)

    # Horizontal parts: the first one between two neighbouring columns on the
    # main row, all others in the first row above it with room for them
    rows: list[list[tuple[float, float]]] = []
    main_used: set[int] = set()
    for idx, rotation, lo, hi in series:
        comp, sym = components[idx], symbols[idx]
        right = column_x[hi] if hi > lo else column_x[lo] + _MIN_PITCH
        cx = _snap_to_grid((column_x[lo] + right) / 2)
        if hi == lo + 1 and lo not in main_used:
            main_used.add(lo)
            placed_at[idx] = PlacedComponent(comp, cx, 0.0, rotation, sym)
            continue
        bx1, _, bx2, _ = _local_box(comp, sym, rotation)
        span = (cx + bx1 - _GRID, cx + bx2 + _GRID)
        free = (
            k
            for k, row in enumerate(rows)
            if all(span[1] <= a or span[0] >= b for a, b in row)
        )
        k = next(free, len(rows))
        if k == len(rows):
            rows.append([])
        rows[k].append(span)
        y = -(k + 1) * _ROW_STEP
        placed_at[idx] = PlacedComponent(comp, cx, y, rotation, sym)

    # Pull-ups stand above everything else, so the supply forms a rail on top
    ceiling = min((_body_box(pc)[1] for pc in placed_at.values()), default=0.0)
    ceiling = min(ceiling, -_GRID)
    for items in pullups.values():
        for idx, rotation, pin in items:
            comp, sym = components[idx], symbols[idx]
            _, _, _, by2 = _local_box(comp, sym, rotation)
            bottom = pin_rel(idx, rotation, pin)[1]
            oy = min(ceiling - _DROP - bottom, ceiling - _GRID - by2)
            oy = -_ceil_to_grid(-oy)
            placed_at[idx] = PlacedComponent(comp, pullup_x[idx], oy, rotation, sym)

    placed = [placed_at[i] for i in range(len(components))]

    # Move the drawing to the top-left corner of the sheet
    if placed:
        boxes = [_body_box(pc) for pc in placed]
        dx = _snap_to_grid(_ORIGIN[0] - min(b[0] for b in boxes) + _GRID / 2)
        dy = _snap_to_grid(_ORIGIN[1] - min(b[1] for b in boxes) + _GRID / 2)
        for pc in placed:
            pc.x, pc.y = _mm(pc.x + dx), _mm(pc.y + dy)
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


def _obstacle_boxes(
    placed: PlacedComponent,
) -> list[tuple[float, float, float, float]]:
    """Boxes covered by a placed component: its symbol body and the text of
    its Reference and Value fields (about 1.27 mm per character).

    Each box is (x_min, y_min, x_max, y_max).
    """
    pins = _pin_positions(placed)
    sym = placed.symbol_info
    half = sym.body_half
    xs = [p[0] for p in pins] + [placed.x - half, placed.x + half]
    ys = [p[1] for p in pins] + [placed.y - half, placed.y + half]
    if placed.component.comp_type == "X":
        right, top, bottom = _subckt_box(len(sym.pin_numbers), sym.pin_names)
        xs += [placed.x - 5.08, placed.x + right]
        ys += [placed.y - top, placed.y - bottom]
    boxes = [(min(xs), min(ys), max(xs), max(ys))]
    ref_pos, value_pos, justify = _field_positions(placed)
    for (fx, fy), text in (
        (ref_pos, placed.component.ref),
        (value_pos, placed.component.value),
    ):
        width = len(text) * 1.27
        x1, x2 = (
            (fx, fx + width) if justify == "left" else (fx - width / 2, fx + width / 2)
        )
        boxes.append((x1, fy - 0.635, x2, fy + 0.635))
    return boxes


def _body_box(placed: PlacedComponent) -> tuple[float, float, float, float]:
    """Box around everything a placed component draws (see _obstacle_boxes).

    Returns (x_min, y_min, x_max, y_max).
    """
    boxes = _obstacle_boxes(placed)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


_STEP = 1.27  # routing grid: every pin lies on a multiple of 1.27 mm
_DIRS = ((1, 0), (0, -1), (-1, 0), (0, 1))  # right, up, left, down (Y down)
_ANGLE_DIR = {0: 0, 90: 1, 180: 2, 270: 3}  # pin direction -> _DIRS index
_BEND_COST = 4
_CROSS_COST = 6
_NEAR_COST = 2  # running right next to another net

Cell = tuple[int, int]


def _cell(p: tuple[float, float]) -> Cell:
    return round(p[0] / _STEP), round(p[1] / _STEP)


def _box_cells(box: tuple[float, float, float, float]) -> set[Cell]:
    """Grid cells covered by a box (edges included)."""
    x1, y1, x2, y2 = box
    return {
        (ix, iy)
        for ix in range(math.ceil(x1 / _STEP - 1e-6), math.floor(x2 / _STEP + 1e-6) + 1)
        for iy in range(math.ceil(y1 / _STEP - 1e-6), math.floor(y2 / _STEP + 1e-6) + 1)
    }


class _Router:
    """Maze router on a 1.27 mm grid for the wires of one schematic.

    KiCad connects every pin, wire end or label that touches a wire, so the
    router only lets a net cross a wire of another net straight through at a
    right angle, never bend, end or run along it, and never touch a pin of
    another net. Component bodies, their text and GND symbols are obstacles.
    """

    def __init__(self, placed: list[PlacedComponent]) -> None:
        self.blocked: set[Cell] = set()
        self.pin_net: dict[Cell, str] = {}
        self.pin_dir: dict[Cell, int] = {}
        # Pin leads and the first cell outside the body: only for that net
        self.reserved: dict[Cell, str] = {}
        # cell -> net -> axes used there ("h"/"v"); nodes are ends/corners
        self.wires: dict[Cell, dict[str, set[str]]] = defaultdict(dict)
        self.nodes: dict[Cell, set[str]] = defaultdict(set)
        boxes: list[set[Cell]] = []
        for pc in placed:
            cells: set[Cell] = set()
            for box in _obstacle_boxes(pc):
                cells |= _box_cells(box)
            boxes.append(cells)
            self.blocked |= cells
        for pc, cells in zip(placed, boxes, strict=True):
            for pos, direction, node in zip(
                _pin_positions(pc),
                _pin_directions(pc),
                pc.component.nodes,
                strict=False,
            ):
                tip = _cell(pos)
                net = "0" if _is_ground(node) else node
                self.pin_net[tip] = net
                self.pin_dir[tip] = _ANGLE_DIR[direction]
                # Free a corridor from the pin tip out of its own body
                dx, dy = _DIRS[_ANGLE_DIR[direction]]
                c = tip
                while c in cells or c == tip:
                    self.blocked.discard(c)
                    self.reserved.setdefault(c, net)
                    c = (c[0] + dx, c[1] + dy)
                if not any(c in other for other in boxes):
                    self.blocked.discard(c)
                    self.reserved.setdefault(c, net)
                if net == "0":
                    # GND symbol: 2.54 mm long, 2.54 mm wide, pointing outward
                    for step in range(1, 3):
                        for side in (-1, 0, 1):
                            self.blocked.add(
                                (
                                    tip[0] + dx * step + dy * side,
                                    tip[1] + dy * step + dx * side,
                                )
                            )
        all_cells = set().union(*boxes, self.pin_net) if boxes else set()
        xs = [c[0] for c in all_cells] or [0]
        ys = [c[1] for c in all_cells] or [0]
        margin = 12
        self.bounds = (
            min(xs) - margin,
            min(ys) - margin,
            max(xs) + margin,
            max(ys) + margin,
        )

    def _near_cost(self, cell: Cell, net: str) -> int:
        """Extra cost for passing close to a wire or pin of another net:
        ``_NEAR_COST`` right next to it, half of it one cell further away."""
        cost = 0
        for dist, weight in ((1, _NEAR_COST), (2, _NEAR_COST // 2)):
            for dx, dy in _DIRS:
                n = (cell[0] + dx * dist, cell[1] + dy * dist)
                if self.pin_net.get(n, net) != net or any(
                    other != net for other in self.wires.get(n, {})
                ):
                    cost = max(cost, weight)
        return cost

    def _crossable(self, cell: Cell, net: str, d: int) -> bool:
        """True if ``net`` may pass straight through ``cell`` moving in ``d``."""
        others = {n: a for n, a in self.wires.get(cell, {}).items() if n != net}
        if not others:
            return True
        if len(others) > 1 or cell in self.nodes:
            return False
        (axes,) = others.values()
        return axes == {"v" if d in (0, 2) else "h"}

    def route(self, net: str, start: Cell, tree: set[Cell]) -> list[Cell] | None:
        """Shortest path from the pin at ``start`` to any cell of ``tree``."""
        x1, y1, x2, y2 = self.bounds
        tx = [c[0] for c in tree]
        ty = [c[1] for c in tree]
        lo_x, hi_x, lo_y, hi_y = min(tx), max(tx), min(ty), max(ty)

        def h(c: Cell) -> int:
            return max(lo_x - c[0], 0, c[0] - hi_x) + max(lo_y - c[1], 0, c[1] - hi_y)

        d0 = self.pin_dir[start]
        heap: list[tuple[int, int, Cell, int]] = [(h(start), 0, start, d0)]
        came: dict[tuple[Cell, int], tuple[Cell, int] | None] = {(start, d0): None}
        best: dict[tuple[Cell, int], int] = {(start, d0): 0}
        while heap:
            _, cost, cell, d = heapq.heappop(heap)
            if cost > best.get((cell, d), math.inf):
                continue
            crossing = any(n != net for n in self.wires.get(cell, {}))
            for m, (dx, dy) in enumerate(_DIRS):
                if m == (d + 2) % 4:
                    continue
                if m != d and (cell == start or crossing):
                    continue  # leave pins straight, never bend on a crossing
                nxt = (cell[0] + dx, cell[1] + dy)
                if not (x1 <= nxt[0] <= x2 and y1 <= nxt[1] <= y2):
                    continue
                owner = self.pin_net.get(nxt)
                if nxt in tree:
                    # Reach a pin only through its own corridor, and never end
                    # on a cell where another net crosses
                    if owner is not None and self.pin_dir[nxt] != (m + 2) % 4:
                        continue
                    if any(n != net for n in self.wires.get(nxt, {})):
                        continue
                    path = [nxt]
                    key: tuple[Cell, int] | None = (cell, d)
                    while key is not None:
                        path.append(key[0])
                        key = came[key]
                    return path[::-1]
                if nxt in self.blocked or owner is not None:
                    continue
                if self.reserved.get(nxt, net) != net:
                    continue
                if not self._crossable(nxt, net, m):
                    continue
                step = 1 + (_BEND_COST if m != d else 0)
                if any(n != net for n in self.wires.get(nxt, {})):
                    step += _CROSS_COST
                else:
                    step += self._near_cost(nxt, net)
                new_cost = cost + step
                if new_cost < best.get((nxt, m), math.inf):
                    best[(nxt, m)] = new_cost
                    came[(nxt, m)] = (cell, d)
                    heapq.heappush(heap, (new_cost + h(nxt), new_cost, nxt, m))
        return None

    def commit(self, net: str, path: list[Cell]) -> None:
        """Record a routed path; its ends and corners become nodes."""
        for i, (a, b) in enumerate(zip(path, path[1:], strict=False)):
            axis = "h" if a[1] == b[1] else "v"
            for c in (a, b):
                self.wires[c].setdefault(net, set()).add(axis)
            if i > 0:
                prev_axis = "h" if path[i - 1][1] == a[1] else "v"
                if prev_axis != axis:
                    self.nodes[a].add(net)
        for c in (path[0], path[-1]):
            self.nodes[c].add(net)

    def remove(self, net: str) -> None:
        """Rip up all wires of a net."""
        for cell in list(self.wires):
            self.wires[cell].pop(net, None)
            if not self.wires[cell]:
                del self.wires[cell]
        for cell in list(self.nodes):
            self.nodes[cell].discard(net)
            if not self.nodes[cell]:
                del self.nodes[cell]


def _segments(
    edges: set[tuple[Cell, Cell]], stops: set[Cell], net: str = ""
) -> tuple[list[Wire], list[tuple[float, float]]]:
    """Merge unit grid edges of one net into wires; return (wires, junctions).

    Wires are split at every corner, branch and pin so that all connections
    happen at wire ends, which is what KiCad expects.
    """
    neighbours: dict[Cell, set[Cell]] = defaultdict(set)
    for a, b in edges:
        neighbours[a].add(b)
        neighbours[b].add(a)

    def is_stop(c: Cell) -> bool:
        ns = neighbours[c]
        straight = (
            len(ns) == 2 and len({n[0] for n in ns}) * len({n[1] for n in ns}) == 2
        )
        return c in stops or not straight

    wires: list[Wire] = []
    seen: set[tuple[Cell, Cell]] = set()
    for start in neighbours:
        if not is_stop(start):
            continue
        for first in neighbours[start]:
            if (start, first) in seen:
                continue
            prev, cur = start, first
            seen.add((prev, cur))
            while not is_stop(cur):
                (nxt,) = neighbours[cur] - {prev}
                prev, cur = cur, nxt
                seen.add((prev, cur))
            seen.add((cur, prev))
            wires.append(
                Wire(
                    (_mm(start[0] * _STEP), _mm(start[1] * _STEP)),
                    (_mm(cur[0] * _STEP), _mm(cur[1] * _STEP)),
                    net,
                )
            )
    junctions = [
        (_mm(c[0] * _STEP), _mm(c[1] * _STEP))
        for c, ns in neighbours.items()
        if len(ns) + (1 if c in stops else 0) >= 3
    ]
    return wires, junctions


def _route_nets(
    placed_components: list[PlacedComponent],
) -> tuple[list[Wire], list[tuple[float, float]], set[str]]:
    """Route wires between pins sharing the same net.

    Nets are routed one after another with ``_Router``, short ones first and
    nets fed by a DC source (supplies) last. A net whose pins cannot all be
    connected is ripped up again and returned as unrouted; it gets a net
    label on every pin instead.

    Returns (wires, junctions, unrouted_nets).
    """
    router = _Router(placed_components)
    net_pins = _net_pins(placed_components)
    supplies = _supply_nets([pc.component for pc in placed_components])

    def order(item: tuple[str, list[tuple[float, float]]]) -> tuple[bool, float]:
        net, pins = item
        xs, ys = [p[0] for p in pins], [p[1] for p in pins]
        return net in supplies, max(xs) - min(xs) + max(ys) - min(ys)

    routed: dict[str, list[list[Cell]]] = {}
    unrouted: set[str] = set()
    for net, pins in sorted(net_pins.items(), key=order):
        if len(pins) < 2:
            continue
        cells = sorted({_cell(p) for p in pins})
        tree = {cells[0]}
        paths: list[list[Cell]] = []
        todo = cells[1:]
        while todo:
            # Connect the pin closest to what is already routed
            todo.sort(
                key=lambda c: min(abs(c[0] - t[0]) + abs(c[1] - t[1]) for t in tree)
            )
            start = todo.pop(0)
            path = router.route(net, start, tree)
            if path is None:
                break
            router.commit(net, path)
            paths.append(path)
            tree.update(path)
        else:
            routed[net] = paths
            continue
        router.remove(net)
        unrouted.add(net)

    wires: list[Wire] = []
    junctions: list[tuple[float, float]] = []
    for net, paths in routed.items():
        edges = {(a, b) for path in paths for a, b in zip(path, path[1:], strict=False)}
        stops = {_cell(p) for p in net_pins[net]}
        net_wires, net_junctions = _segments(edges, stops, net)
        wires += net_wires
        junctions += net_junctions
    return wires, junctions, unrouted


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
    _polyline([(-4.318, 3.429), (-3.302, 3.429)]),
    _polyline([(-3.81, 2.921), (-3.81, 3.937)]),
    _polyline([(-4.318, -3.429), (-3.302, -3.429)]),
]


def _source_symbol(lib_id: str, ref: str, marks: list[str], control: bool) -> str:
    """Diamond-shaped controlled/behavioral source.

    Output pins N+ (1, top) and N- (2, bottom); with ``control`` also C+ (3,
    upper) and C- (4, lower) pointing left, numbered like KiCad's
    ESOURCE/GSOURCE.
    """
    graphics = [_polyline(_DIAMOND, "background"), *marks]
    pins = [_pin("1", 0, 5.08, 270), _pin("2", 0, -5.08, 90)]
    if control:
        graphics += _CONTROL_MARKS
        pins += [
            _pin("3", -7.62, 2.54, 0, "input"),
            _pin("4", -7.62, -2.54, 0, "input"),
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


def _lib_symbol_text(lib_id: str, pin_names: list[str] | None = None) -> str | None:
    """Return the lib symbol definition for a lib_id (subcircuits generated).

    ``pin_names`` are required for subcircuits with named ports.
    """
    if pin_names:
        return _subckt_symbol(len(pin_names), lib_id, pin_names)
    if lib_id in _LIB_SYMBOL_TEMPLATES:
        return _LIB_SYMBOL_TEMPLATES[lib_id]
    if lib_id == _subckt_lib_id(_SUBCKT_MIN_PINS):
        return _subckt_symbol(_SUBCKT_MIN_PINS)
    prefix = "Simulation_SPICE:SUBCKT_"
    if lib_id.startswith(prefix) and lib_id[len(prefix) :].isdigit():
        return _subckt_symbol(int(lib_id[len(prefix) :]))
    return None


def _build_lib_symbols(
    used_lib_ids: set[str],
    include_gnd: bool = False,
    pin_names: dict[str, list[str]] | None = None,
) -> str:
    """Assemble lib_symbols section with only the templates actually used.

    ``pin_names`` maps the lib_ids of named subcircuit symbols to their ports.
    """
    parts: list[str] = []
    parts.append("  (lib_symbols")
    for lib_id in sorted(used_lib_ids):
        text = _lib_symbol_text(lib_id, (pin_names or {}).get(lib_id))
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


def _escape(text: str) -> str:
    """Escape a string for use inside a quoted S-expression string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


# Landscape paper sizes in mm; the usable area leaves room for the frame
# and, at the bottom, for the title block
_PAPER_SIZES = (("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594))


def _paper_size(placed: list[PlacedComponent]) -> str:
    """Smallest landscape paper size the drawing fits on (A0 at most)."""
    if not placed:
        return "A4"
    boxes = [_body_box(pc) for pc in placed]
    # GND symbols reach up to 5.08 mm beyond a pin
    right = max(b[2] for b in boxes) + 5.08
    bottom = max(b[3] for b in boxes) + 5.08
    for name, width, height in _PAPER_SIZES:
        if right <= width - 15 and bottom <= height - 45:
            return name
    return "A0"


def _emit_header(sheet_uuid: str, paper: str = "A4") -> str:
    """Emit the file header."""
    return f"""\
(kicad_sch
  (version 20231120)
  (generator "spicebridge")
  (generator_version "0.1")
  (uuid "{sheet_uuid}")
  (paper "{paper}")
"""


def _field_positions(
    placed: PlacedComponent,
) -> tuple[tuple[float, float], tuple[float, float], str]:
    """Positions of the Reference and Value fields, plus their justification.

    Subcircuits get the fields centred above and below the box, horizontal
    2-pin parts above and below the body, everything else to the right.
    """
    x, y = placed.x, placed.y
    sym = placed.symbol_info
    if placed.component.comp_type == "X":
        right, top, bottom = _subckt_box(len(sym.pin_numbers), sym.pin_names)
        cx = _mm(x + (right - 5.08) / 2)
        return (cx, _mm(y - top - 1.27)), (cx, _mm(y - bottom + 1.27)), ""
    pins = _pin_positions(placed)
    if len(pins) == 2 and pins[0][1] == pins[1][1]:
        dy = sym.body_half + 1.27
        return (x, _mm(y - dy)), (x, _mm(y + dy)), ""
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
    # KiCad adds the symbol rotation to the field angle; compensate so the
    # text of rotated parts stays horizontal. A resulting angle of 180/270 is
    # drawn as 0/90 with mirrored justification, so mirror it back.
    fa = 90 if angle in (90, 270) else 0
    if (fa + angle) % 360 in (180, 270):
        justify = {"left": "right", "right": "left"}.get(justify, justify)
    effects = "(effects (font (size 1.27 1.27))" + (
        f" (justify {justify}))" if justify else ")"
    )
    props = [
        f'      (property "Reference" "{comp.ref}" (at {rx} {ry} {fa})'
        f"\n        {effects})",
        f'      (property "Value" "{_escape(comp.value)}" (at {vx} {vy} {fa})'
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

    scene = build_scene(netlist)
    sheet_uuid = _uid()

    # Build output
    parts: list[str] = []

    # Header
    parts.append(_emit_header(sheet_uuid, _paper_size(scene.placed)))

    # Lib symbols
    parts.append("  (lib_symbols")
    parts.extend(scene.lib_symbols.values())
    parts.append("  )")
    parts.append("")

    # Symbol instances
    for pc in scene.placed:
        parts.append(_emit_symbol_instance(pc, sheet_uuid))
        parts.append("")

    # Wires
    for wire in scene.wires:
        parts.append(_emit_wire(wire))

    # Junctions
    for jx, jy in scene.junctions:
        parts.append(_emit_junction(jx, jy))

    # Ground power symbols
    for gx, gy, direction in scene.ground_pins:
        parts.append(_emit_power_symbol(gx, gy, sheet_uuid, direction))
        parts.append("")

    # Net labels
    for lx, ly, name, direction in scene.net_labels:
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

    return output_path, scene.warnings


@dataclass
class Scene:
    """A laid-out and routed schematic, ready to be written or drawn."""

    placed: list[PlacedComponent]
    wires: list[Wire]
    junctions: list[tuple[float, float]]
    # (x, y, pin direction) of every pin tied to ground
    ground_pins: list[tuple[float, float, int]]
    # (x, y, net name, pin direction)
    net_labels: list[tuple[float, float, str, int]]
    # lib_id -> lib symbol definition, including power:GND when used
    lib_symbols: dict[str, str]
    warnings: list[str]


def build_scene(netlist: str) -> Scene:
    """Parse, place and route a SPICE netlist.

    This is the common ground of the KiCad export and the SVG renderer, so
    both show the same drawing.

    Raises:
        ValueError: If the netlist contains no components.
    """
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

    subckt_ports = _subckt_ports(netlist)

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
        if comp.comp_type in ("E", "G") and comp.value.lower().startswith("poly"):
            warnings.append(
                f"{comp.ref}: controlling nodes of POLY sources are not drawn; "
                f"they are listed in the value field"
            )
        if comp.comp_type == "X" and comp.value:
            subckt = comp.value.split()[0].lower()
            ports = subckt_ports.get(subckt)
            if ports is not None and len(ports) != len(comp.nodes):
                warnings.append(
                    f"{comp.ref}: {len(comp.nodes)} nodes but subcircuit "
                    f"'{subckt}' has {len(ports)} ports; pins are numbered"
                )
        drawn.append(comp)
    components = drawn

    # Layout
    placed = _layout_components(components, _model_types(netlist), subckt_ports)

    # Wire routing
    wires, junctions, unrouted = _route_nets(placed)

    # Ground symbols
    ground_pins = _find_ground_pins(placed)

    # Net labels
    net_labels = _find_net_labels(placed, unrouted)

    # Lib symbols of the parts in use
    pin_names = {
        pc.symbol_info.lib_id: pc.symbol_info.pin_names
        for pc in placed
        if pc.symbol_info.pin_names
    }
    lib_symbols: dict[str, str] = {}
    for lib_id in sorted({pc.symbol_info.lib_id for pc in placed}):
        text = _lib_symbol_text(lib_id, pin_names.get(lib_id))
        if text is not None:
            lib_symbols[lib_id] = text
    if ground_pins:
        lib_symbols["power:GND"] = _GND_LIB_SYMBOL

    return Scene(
        placed, wires, junctions, ground_pins, net_labels, lib_symbols, warnings
    )
