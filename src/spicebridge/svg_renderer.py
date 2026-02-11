"""Interactive SVG schematic renderer with data attributes for web viewer."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field

from spicebridge.schematic import ParsedComponent, _is_ground, parse_netlist

# ---------------------------------------------------------------------------
# Layout constants (pixel coordinates)
# ---------------------------------------------------------------------------

_COL_SOURCES = 100
_COL_SERIES = 300
_COL_SHUNT = 500
_Y_START = 100
_Y_SPACING = 120

# Pin offsets per component type (dx, dy from center)
_PIN_OFFSETS: dict[str, list[tuple[float, float]]] = {
    "R": [(0, -40), (0, 40)],
    "C": [(0, -30), (0, 30)],
    "L": [(0, -40), (0, 40)],
    "V": [(0, -40), (0, 40)],
    "I": [(0, -40), (0, 40)],
    "D": [(0, -30), (0, 30)],
    "Q": [(-25, 0), (0, -30), (0, 30)],  # B, C, E
    "M": [(-25, 0), (0, -30), (0, 30)],  # G, D, S
    "X": [(-30, -15), (-30, 15), (30, -15), (30, 15)],
}


@dataclass
class _PlacedComponent:
    """A component with assigned SVG position."""

    component: ParsedComponent
    x: float
    y: float
    rotation: float  # degrees
    pin_offsets: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class _Wire:
    """A wire segment between two points."""

    start: tuple[float, float]
    end: tuple[float, float]
    node: str


# ---------------------------------------------------------------------------
# Component symbol drawers — each returns a list of SVG sub-elements
# ---------------------------------------------------------------------------

_NS = "http://www.w3.org/2000/svg"


def _resistor_symbol() -> list[ET.Element]:
    """Zigzag resistor symbol, pins at (0,-40) and (0,40)."""
    path = ET.Element(
        "path",
        {
            "d": "M 0,-40 L 0,-25 L 8,-20 L -8,-10 L 8,0 L -8,10 L 8,20 L 0,25 L 0,40",
            "class": "comp-body",
        },
    )
    return [path]


def _capacitor_symbol() -> list[ET.Element]:
    """Parallel-plate capacitor, pins at (0,-30) and (0,30)."""
    elems = []
    # Lead wires
    elems.append(
        ET.Element(
            "line",
            {"x1": "0", "y1": "-30", "x2": "0", "y2": "-5", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "line", {"x1": "0", "y1": "5", "x2": "0", "y2": "30", "class": "comp-body"}
        )
    )
    # Plates
    elems.append(
        ET.Element(
            "line",
            {
                "x1": "-10",
                "y1": "-5",
                "x2": "10",
                "y2": "-5",
                "class": "comp-body",
                "stroke-width": "3",
            },
        )
    )
    elems.append(
        ET.Element(
            "line",
            {
                "x1": "-10",
                "y1": "5",
                "x2": "10",
                "y2": "5",
                "class": "comp-body",
                "stroke-width": "3",
            },
        )
    )
    return elems


def _inductor_symbol() -> list[ET.Element]:
    """Semicircular bump inductor, pins at (0,-40) and (0,40)."""
    # 4 semicircular bumps
    d = "M 0,-40 L 0,-28"
    for i in range(4):
        cy = -20 + i * 14
        d += f" A 7,7 0 0,1 0,{cy + 14}"
    d += " L 0,40"
    path = ET.Element("path", {"d": d, "class": "comp-body"})
    return [path]


def _voltage_source_symbol() -> list[ET.Element]:
    """Circle with +/-, pins at (0,-40) and (0,40)."""
    elems = []
    elems.append(
        ET.Element(
            "line",
            {"x1": "0", "y1": "-40", "x2": "0", "y2": "-18", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "circle",
            {"cx": "0", "cy": "0", "r": "18", "class": "comp-body", "fill": "none"},
        )
    )
    elems.append(
        ET.Element(
            "line", {"x1": "0", "y1": "18", "x2": "0", "y2": "40", "class": "comp-body"}
        )
    )
    # Plus sign (top)
    plus = ET.Element(
        "text",
        {
            "x": "0",
            "y": "-6",
            "text-anchor": "middle",
            "class": "source-label",
            "font-size": "14",
        },
    )
    plus.text = "+"
    elems.append(plus)
    # Minus sign (bottom)
    minus = ET.Element(
        "text",
        {
            "x": "0",
            "y": "12",
            "text-anchor": "middle",
            "class": "source-label",
            "font-size": "14",
        },
    )
    minus.text = "\u2013"
    elems.append(minus)
    return elems


def _current_source_symbol() -> list[ET.Element]:
    """Circle with arrow, pins at (0,-40) and (0,40)."""
    elems = []
    elems.append(
        ET.Element(
            "line",
            {"x1": "0", "y1": "-40", "x2": "0", "y2": "-18", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "circle",
            {"cx": "0", "cy": "0", "r": "18", "class": "comp-body", "fill": "none"},
        )
    )
    elems.append(
        ET.Element(
            "line", {"x1": "0", "y1": "18", "x2": "0", "y2": "40", "class": "comp-body"}
        )
    )
    # Arrow pointing up
    elems.append(
        ET.Element(
            "line",
            {"x1": "0", "y1": "10", "x2": "0", "y2": "-10", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "path",
            {"d": "M -5,-5 L 0,-10 L 5,-5", "class": "comp-body", "fill": "none"},
        )
    )
    return elems


def _diode_symbol() -> list[ET.Element]:
    """Triangle + bar diode, pins at (0,-30) and (0,30)."""
    elems = []
    elems.append(
        ET.Element(
            "line",
            {"x1": "0", "y1": "-30", "x2": "0", "y2": "-10", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "path",
            {"d": "M -10,-10 L 10,-10 L 0,10 Z", "class": "comp-body", "fill": "none"},
        )
    )
    elems.append(
        ET.Element(
            "line",
            {
                "x1": "-10",
                "y1": "10",
                "x2": "10",
                "y2": "10",
                "class": "comp-body",
                "stroke-width": "2",
            },
        )
    )
    elems.append(
        ET.Element(
            "line", {"x1": "0", "y1": "10", "x2": "0", "y2": "30", "class": "comp-body"}
        )
    )
    return elems


def _bjt_symbol() -> list[ET.Element]:
    """BJT NPN: vertical bar + diagonal leads, arrow on emitter. Pins: B(-25,0), C(0,-30), E(0,30)."""
    elems = []
    # Base lead
    elems.append(
        ET.Element(
            "line",
            {"x1": "-25", "y1": "0", "x2": "-8", "y2": "0", "class": "comp-body"},
        )
    )
    # Vertical bar
    elems.append(
        ET.Element(
            "line",
            {
                "x1": "-8",
                "y1": "-15",
                "x2": "-8",
                "y2": "15",
                "class": "comp-body",
                "stroke-width": "3",
            },
        )
    )
    # Collector lead
    elems.append(
        ET.Element(
            "line",
            {"x1": "-8", "y1": "-10", "x2": "0", "y2": "-30", "class": "comp-body"},
        )
    )
    # Emitter lead with arrow
    elems.append(
        ET.Element(
            "line",
            {"x1": "-8", "y1": "10", "x2": "0", "y2": "30", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "path",
            {"d": "M -2,22 L 0,30 L -6,26", "class": "comp-body", "fill": "none"},
        )
    )
    return elems


def _mosfet_symbol() -> list[ET.Element]:
    """NMOS: gate oxide bar + channel. Pins: G(-25,0), D(0,-30), S(0,30)."""
    elems = []
    # Gate lead
    elems.append(
        ET.Element(
            "line",
            {"x1": "-25", "y1": "0", "x2": "-10", "y2": "0", "class": "comp-body"},
        )
    )
    # Gate oxide bar
    elems.append(
        ET.Element(
            "line",
            {
                "x1": "-10",
                "y1": "-15",
                "x2": "-10",
                "y2": "15",
                "class": "comp-body",
                "stroke-width": "2",
            },
        )
    )
    # Channel
    elems.append(
        ET.Element(
            "line",
            {
                "x1": "-6",
                "y1": "-15",
                "x2": "-6",
                "y2": "15",
                "class": "comp-body",
                "stroke-width": "2",
            },
        )
    )
    # Drain lead
    elems.append(
        ET.Element(
            "line",
            {"x1": "-6", "y1": "-10", "x2": "0", "y2": "-10", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "line",
            {"x1": "0", "y1": "-10", "x2": "0", "y2": "-30", "class": "comp-body"},
        )
    )
    # Source lead
    elems.append(
        ET.Element(
            "line",
            {"x1": "-6", "y1": "10", "x2": "0", "y2": "10", "class": "comp-body"},
        )
    )
    elems.append(
        ET.Element(
            "line", {"x1": "0", "y1": "10", "x2": "0", "y2": "30", "class": "comp-body"}
        )
    )
    return elems


def _ground_symbol(x: float, y: float) -> ET.Element:
    """Three decreasing horizontal lines ground symbol."""
    g = ET.Element("g", {"class": "ground-symbol"})
    ET.SubElement(
        g,
        "line",
        {
            "x1": str(x - 12),
            "y1": str(y),
            "x2": str(x + 12),
            "y2": str(y),
            "class": "comp-body",
        },
    )
    ET.SubElement(
        g,
        "line",
        {
            "x1": str(x - 8),
            "y1": str(y + 5),
            "x2": str(x + 8),
            "y2": str(y + 5),
            "class": "comp-body",
        },
    )
    ET.SubElement(
        g,
        "line",
        {
            "x1": str(x - 4),
            "y1": str(y + 10),
            "x2": str(x + 4),
            "y2": str(y + 10),
            "class": "comp-body",
        },
    )
    return g


def _generic_symbol() -> list[ET.Element]:
    """Rectangular box for subcircuits/unknown, pins spread around edges."""
    rect = ET.Element(
        "rect",
        {
            "x": "-25",
            "y": "-20",
            "width": "50",
            "height": "40",
            "class": "comp-body",
            "fill": "none",
        },
    )
    return [rect]


_SYMBOL_DRAWERS: dict[str, callable] = {
    "R": _resistor_symbol,
    "C": _capacitor_symbol,
    "L": _inductor_symbol,
    "V": _voltage_source_symbol,
    "I": _current_source_symbol,
    "D": _diode_symbol,
    "Q": _bjt_symbol,
    "M": _mosfet_symbol,
    "X": _generic_symbol,
}


# ---------------------------------------------------------------------------
# Layout engine
# ---------------------------------------------------------------------------


def _layout_components(
    components: list[ParsedComponent],
) -> list[_PlacedComponent]:
    """Assign positions using column-based layout."""
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

    placed: list[_PlacedComponent] = []

    # Sources: vertical at left column
    for i, comp in enumerate(sources):
        offsets = _PIN_OFFSETS.get(comp.comp_type, [(0, -40), (0, 40)])
        y = _Y_START + i * _Y_SPACING
        placed.append(_PlacedComponent(comp, _COL_SOURCES, y, 0, list(offsets)))

    # Series: rotated 90 degrees (horizontal) at middle column
    for i, comp in enumerate(series):
        offsets = _PIN_OFFSETS.get(comp.comp_type, [(0, -40), (0, 40)])
        y = _Y_START + i * _Y_SPACING
        placed.append(_PlacedComponent(comp, _COL_SERIES, y, 90, list(offsets)))

    # Shunt: vertical at right column
    for i, comp in enumerate(shunt):
        offsets = _PIN_OFFSETS.get(comp.comp_type, [(0, -40), (0, 40)])
        y = _Y_START + i * _Y_SPACING
        placed.append(_PlacedComponent(comp, _COL_SHUNT, y, 0, list(offsets)))

    return placed


def _compute_pin_positions(
    placed: _PlacedComponent,
) -> list[tuple[float, float]]:
    """Compute absolute pin positions for a placed component."""
    angle_rad = math.radians(placed.rotation)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    positions: list[tuple[float, float]] = []
    n_pins = min(len(placed.component.nodes), len(placed.pin_offsets))
    for i in range(n_pins):
        dx, dy = placed.pin_offsets[i]
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        positions.append((placed.x + rx, placed.y + ry))

    return positions


# ---------------------------------------------------------------------------
# Wire routing
# ---------------------------------------------------------------------------


def _route_wires(
    placed_components: list[_PlacedComponent],
) -> tuple[list[_Wire], list[tuple[float, float]]]:
    """Route wires between pins sharing the same net. Manhattan L-routing."""
    net_pins: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for pc in placed_components:
        pins = _compute_pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins) and not _is_ground(node):
                net_pins[node].append(pins[idx])

    wires: list[_Wire] = []
    junctions: list[tuple[float, float]] = []

    for net, pin_list in net_pins.items():
        if len(pin_list) < 2:
            continue
        pin_list.sort()
        for i in range(len(pin_list) - 1):
            x1, y1 = pin_list[i]
            x2, y2 = pin_list[i + 1]
            if x1 != x2 and y1 != y2:
                # L-route: horizontal then vertical
                wires.append(_Wire((x1, y1), (x2, y1), net))
                wires.append(_Wire((x2, y1), (x2, y2), net))
                if i > 0:
                    junctions.append((x1, y1))
            else:
                wires.append(_Wire((x1, y1), (x2, y2), net))
                if i > 0:
                    junctions.append((x1, y1))
        if len(pin_list) > 2:
            junctions.append(pin_list[1])

    return wires, junctions


def _find_ground_pins(
    placed_components: list[_PlacedComponent],
) -> list[tuple[float, float]]:
    """Find pin positions connected to ground."""
    ground_positions: list[tuple[float, float]] = []
    for pc in placed_components:
        pins = _compute_pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins) and _is_ground(node):
                ground_positions.append(pins[idx])
    return ground_positions


def _find_net_labels(
    placed_components: list[_PlacedComponent],
) -> list[tuple[float, float, str]]:
    """Find named nets and return label positions."""
    net_pins: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for pc in placed_components:
        pins = _compute_pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins) and not _is_ground(node):
                net_pins[node].append(pins[idx])

    labels: list[tuple[float, float, str]] = []
    for net_name, pin_list in net_pins.items():
        if net_name.isdigit():
            continue
        if pin_list:
            x, y = pin_list[0]
            labels.append((x, y, net_name))
    return labels


# ---------------------------------------------------------------------------
# SVG builder
# ---------------------------------------------------------------------------


def _build_svg(
    placed: list[_PlacedComponent],
    wires: list[_Wire],
    junctions: list[tuple[float, float]],
    ground_pins: list[tuple[float, float]],
    net_labels: list[tuple[float, float, str]],
    results: dict | None,
    width: int,
    height: int,
) -> ET.Element:
    """Assemble the complete SVG element tree."""
    # Compute bounding box
    all_x: list[float] = []
    all_y: list[float] = []

    for pc in placed:
        pins = _compute_pin_positions(pc)
        for px, py in pins:
            all_x.append(px)
            all_y.append(py)
        all_x.append(pc.x)
        all_y.append(pc.y)

    for w in wires:
        all_x.extend([w.start[0], w.end[0]])
        all_y.extend([w.start[1], w.end[1]])

    for gx, gy in ground_pins:
        all_x.append(gx)
        all_y.append(gy + 15)  # ground symbol extends down

    padding = 60
    if all_x and all_y:
        min_x = min(all_x) - padding
        min_y = min(all_y) - padding
        max_x = max(all_x) + padding
        max_y = max(all_y) + padding
    else:
        min_x, min_y = 0, 0
        max_x, max_y = width, height

    vb_w = max_x - min_x
    vb_h = max_y - min_y

    svg = ET.Element(
        "svg",
        {
            "xmlns": _NS,
            "width": str(width),
            "height": str(height),
            "viewBox": f"{min_x} {min_y} {vb_w} {vb_h}",
            "class": "spicebridge-schematic",
        },
    )

    # Stylesheet
    style = ET.SubElement(svg, "style")
    style.text = """
        .comp-body { stroke: #4fc3f7; stroke-width: 1.5; fill: none; }
        .component:hover .comp-body { stroke: #ffeb3b; }
        .component:hover .comp-label { fill: #ffeb3b; }
        .comp-label { fill: #81c784; font-family: monospace; font-size: 12px; }
        .wire { stroke: #4fc3f7; stroke-width: 1.5; }
        .wire:hover { stroke: #ffeb3b; }
        .node-dot { fill: #4fc3f7; }
        .ground-symbol .comp-body { stroke: #4fc3f7; stroke-width: 1.5; }
        .net-label { fill: #ce93d8; font-family: monospace; font-size: 11px; }
        .sim-annotation { fill: #ff8a65; font-family: monospace; font-size: 10px; }
        .source-label { fill: #81c784; font-family: monospace; }
        .highlight .comp-body { stroke: #ffeb3b !important; }
        .highlight .comp-label { fill: #ffeb3b !important; }
        .highlight { stroke: #ffeb3b !important; }
    """

    # Background
    ET.SubElement(
        svg,
        "rect",
        {
            "x": str(min_x),
            "y": str(min_y),
            "width": str(vb_w),
            "height": str(vb_h),
            "fill": "#1e1e1e",
        },
    )

    # Build a map of node positions for annotations
    node_positions: dict[str, tuple[float, float]] = {}

    # Draw components
    for pc in placed:
        comp = pc.component
        g = ET.SubElement(
            svg,
            "g",
            {
                "id": f"component-{comp.ref}",
                "data-ref": comp.ref,
                "data-type": comp.comp_type,
                "data-value": comp.value,
                "class": "component",
                "transform": f"translate({pc.x},{pc.y}) rotate({pc.rotation})",
            },
        )

        # Draw symbol
        drawer = _SYMBOL_DRAWERS.get(comp.comp_type, _generic_symbol)
        for elem in drawer():
            g.append(elem)

        # Component label (outside rotation group so text is upright)
        label = ET.SubElement(
            svg,
            "text",
            {
                "x": str(pc.x + 15),
                "y": str(pc.y - 5),
                "class": "comp-label",
                "data-ref": comp.ref,
            },
        )
        label.text = f"{comp.ref}: {comp.value}"

        # Track pin positions for node map
        pins = _compute_pin_positions(pc)
        for idx, node in enumerate(pc.component.nodes):
            if idx < len(pins):
                node_positions.setdefault(node, pins[idx])

    # Draw wires
    for w in wires:
        ET.SubElement(
            svg,
            "line",
            {
                "x1": str(w.start[0]),
                "y1": str(w.start[1]),
                "x2": str(w.end[0]),
                "y2": str(w.end[1]),
                "class": "wire",
                "data-node": w.node,
            },
        )

    # Draw junction dots (node-dots)
    for jx, jy in junctions:
        ET.SubElement(
            svg,
            "circle",
            {
                "cx": str(jx),
                "cy": str(jy),
                "r": "4",
                "class": "node-dot",
            },
        )

    # Draw ground symbols
    for gx, gy in ground_pins:
        svg.append(_ground_symbol(gx, gy))

    # Draw net labels
    for lx, ly, name in net_labels:
        label = ET.SubElement(
            svg,
            "text",
            {
                "x": str(lx),
                "y": str(ly - 10),
                "class": "net-label",
                "data-node": name,
            },
        )
        label.text = name

    # Simulation result annotations
    if results:
        nodes_data = results.get("nodes", {})
        for node_name, voltage in nodes_data.items():
            if node_name in node_positions:
                nx, ny = node_positions[node_name]
                if isinstance(voltage, (int, float)):
                    ann = ET.SubElement(
                        svg,
                        "text",
                        {
                            "x": str(nx + 5),
                            "y": str(ny + 18),
                            "class": "sim-annotation",
                            "data-node": node_name,
                        },
                    )
                    ann.text = f"{voltage:.4g}V"

    return svg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_svg(
    netlist: str,
    results: dict | None = None,
    width: int = 800,
    height: int = 600,
) -> str:
    """Render a SPICE netlist as an interactive SVG string.

    Args:
        netlist: SPICE netlist string.
        results: Optional simulation results dict for overlay annotations.
        width: SVG width in pixels.
        height: SVG height in pixels.

    Returns:
        SVG document as a string.
    """
    components = parse_netlist(netlist)

    # Handle MOSFET 4-pin -> 3-pin
    for comp in components:
        if comp.comp_type == "M" and len(comp.nodes) == 4:
            comp.nodes = comp.nodes[:3]

    if not components:
        # Return a valid empty SVG
        svg = ET.Element(
            "svg",
            {
                "xmlns": _NS,
                "width": str(width),
                "height": str(height),
                "viewBox": f"0 0 {width} {height}",
                "class": "spicebridge-schematic",
            },
        )
        style = ET.SubElement(svg, "style")
        style.text = ".comp-body { stroke: #4fc3f7; fill: none; }"
        ET.SubElement(
            svg,
            "rect",
            {
                "x": "0",
                "y": "0",
                "width": str(width),
                "height": str(height),
                "fill": "#1e1e1e",
            },
        )
        return ET.tostring(svg, encoding="unicode", xml_declaration=False)

    placed = _layout_components(components)
    wires, junctions = _route_wires(placed)
    ground_pins = _find_ground_pins(placed)
    net_labels = _find_net_labels(placed)

    svg = _build_svg(
        placed, wires, junctions, ground_pins, net_labels, results, width, height
    )

    return ET.tostring(svg, encoding="unicode", xml_declaration=False)
