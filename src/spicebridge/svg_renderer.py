"""SVG schematic renderer for the web viewer and draw_schematic.

Draws the same placed and routed schematic as the KiCad export
(``kicad_export.build_scene``), using the graphics of the embedded KiCad
library symbols, so the picture on screen and the exported .kicad_sch
match. Elements carry data attributes for the interactive web viewer.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET  # nosec B405 — building SVG, not parsing untrusted XML
from functools import lru_cache

from spicebridge.kicad_export import (
    PlacedComponent,
    Scene,
    _field_positions,
    _obstacle_boxes,
    _pin_directions,
    _pin_positions,
    _rotate,
    build_scene,
)
from spicebridge.schematic import parse_netlist

_NS = "http://www.w3.org/2000/svg"
_PX = 8.0  # SVG user units (pixels) per millimetre
_FONT_MM = 1.27  # KiCad default text size
_PADDING_MM = 5.08

_THEMES: dict[str, dict[str, str]] = {
    # Matches the web viewer's dark UI
    "dark": {
        "background": "#1e1e1e",
        "body": "#4fc3f7",
        "fill": "#263238",
        "wire": "#4fc3f7",
        "label": "#81c784",
        "net": "#ce93d8",
        "annotation": "#ff8a65",
        "highlight": "#ffeb3b",
    },
    # KiCad's default schematic colours
    "light": {
        "background": "#ffffff",
        "body": "#840000",
        "fill": "#ffffc2",
        "wire": "#008400",
        "label": "#006464",
        "net": "#000000",
        "annotation": "#c62828",
        "highlight": "#ff6f00",
    },
}


# ---------------------------------------------------------------------------
# Lib symbol graphics
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')


def _parse_sexp(text: str) -> list:
    """Minimal S-expression parser; strings keep their quotes."""
    stack: list[list] = [[]]
    for tok in _TOKEN_RE.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(tok)
    return stack[0]


def _child(node: list, name: str) -> list | None:
    return next((c for c in node if isinstance(c, list) and c and c[0] == name), None)


def _xy(node: list | None) -> tuple[float, float]:
    return (float(node[1]), float(node[2])) if node else (0.0, 0.0)


def _fill(node: list) -> str:
    fill = _child(node, "fill")
    kind = _child(fill, "type") if fill else None
    return kind[1] if kind else "none"


@lru_cache(maxsize=128)
def _symbol_graphics(text: str) -> tuple[tuple, ...]:
    """Drawing primitives of a lib symbol in lib coordinates (Y up).

    Each primitive is ``(kind, data, fill)``: ``poly`` with a point list,
    ``circle`` with (center, radius), ``arc`` with (start, mid, end) and
    ``pin`` with (tip, angle, length, name, show_name, name_offset).
    """
    (root,) = _parse_sexp(text)
    pin_names = _child(root, "pin_names") or []
    show_names = "hide" not in pin_names
    offset_node = _child(pin_names, "offset") if pin_names else None
    name_offset = float(offset_node[1]) if offset_node else 0.508

    prims: list[tuple] = []

    def walk(node: list) -> None:
        for item in node:
            if not isinstance(item, list) or not item:
                continue
            kind = item[0]
            if kind == "symbol":
                walk(item)
            elif kind == "polyline":
                pts = _child(item, "pts") or []
                points = tuple(_xy(p) for p in pts[1:])
                prims.append(("poly", points, _fill(item)))
            elif kind == "rectangle":
                (x1, y1), (x2, y2) = (
                    _xy(_child(item, "start")),
                    _xy(_child(item, "end")),
                )
                points = ((x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1))
                prims.append(("poly", points, _fill(item)))
            elif kind == "circle":
                radius = float(_child(item, "radius")[1])
                prims.append(
                    ("circle", (_xy(_child(item, "center")), radius), _fill(item))
                )
            elif kind == "arc":
                ends = tuple(_xy(_child(item, k)) for k in ("start", "mid", "end"))
                prims.append(("arc", ends, "none"))
            elif kind == "pin":
                at = _child(item, "at")
                length = float((_child(item, "length") or [0, 0])[1])
                name = (_child(item, "name") or ["", '""'])[1].strip('"')
                data = (_xy(at), float(at[3]), length, name, show_names, name_offset)
                prims.append(("pin", data, "none"))

    walk(root)
    return tuple(prims)


# ---------------------------------------------------------------------------
# SVG drawing
# ---------------------------------------------------------------------------


class _Canvas:
    """SVG element tree in pixels that tracks the drawn area."""

    def __init__(self) -> None:
        self.root = ET.Element("svg", {"xmlns": _NS, "class": "spicebridge-schematic"})
        self.xs: list[float] = []
        self.ys: list[float] = []

    def px(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        x, y = x_mm * _PX, y_mm * _PX
        self.xs.append(x)
        self.ys.append(y)
        return round(x, 2), round(y, 2)

    def text(
        self,
        parent: ET.Element,
        x_mm: float,
        y_mm: float,
        content: str,
        cls: str,
        anchor: str = "start",
        **attrs: str,
    ) -> ET.Element:
        x, y = self.px(x_mm, y_mm)
        width = len(content) * _FONT_MM * 0.8 * _PX
        left = {"start": x, "middle": x - width / 2, "end": x - width}[anchor]
        self.xs += [left, left + width]
        elem = ET.SubElement(
            parent,
            "text",
            {"x": str(x), "y": str(y), "class": cls, "text-anchor": anchor, **attrs},
        )
        elem.text = content
        return elem


def _draw_symbol(
    canvas: _Canvas,
    parent: ET.Element,
    text: str,
    x: float,
    y: float,
    rotation: float,
) -> None:
    """Draw a lib symbol placed at (x, y) mm with the given rotation."""

    def to_px(lx: float, ly: float) -> tuple[float, float]:
        dx, dy = _rotate(lx, -ly, rotation)
        return canvas.px(x + dx, y + dy)

    for kind, data, fill in _symbol_graphics(text):
        cls = "comp-body" + {"background": " fill-bg", "outline": " fill-solid"}.get(
            fill, ""
        )
        if kind == "poly":
            points = " ".join(f"{px},{py}" for px, py in (to_px(*p) for p in data))
            ET.SubElement(parent, "polyline", {"points": points, "class": cls})
        elif kind == "circle":
            (cx, cy), radius = data
            px, py = to_px(cx, cy)
            r = radius * _PX
            canvas.xs += [px - r, px + r]
            canvas.ys += [py - r, py + r]
            ET.SubElement(
                parent,
                "circle",
                {"cx": str(px), "cy": str(py), "r": str(r), "class": cls},
            )
        elif kind == "arc":
            (sx, sy), (mx, my), (ex, ey) = (to_px(*p) for p in data)
            # Quadratic curve through the arc's midpoint
            qx, qy = 2 * mx - (sx + ex) / 2, 2 * my - (sy + ey) / 2
            ET.SubElement(
                parent,
                "path",
                {"d": f"M {sx},{sy} Q {qx:.2f},{qy:.2f} {ex},{ey}", "class": cls},
            )
        elif kind == "pin":
            (tx, ty), angle, length, name, show_name, name_offset = data
            ux, uy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            x1, y1 = to_px(tx, ty)
            x2, y2 = to_px(tx + ux * length, ty + uy * length)
            if length:
                ET.SubElement(
                    parent,
                    "line",
                    {
                        "x1": str(x1),
                        "y1": str(y1),
                        "x2": str(x2),
                        "y2": str(y2),
                        "class": "comp-body",
                    },
                )
            if show_name and name and name != "~":
                # Name inside the body, next to the inner end of the pin
                nx = tx + ux * (length + name_offset)
                ny = ty + uy * (length + name_offset)
                dx, dy = _rotate(nx, -ny, rotation)
                sdx, _ = _rotate(ux, -uy, rotation)
                anchor = "start" if sdx > 0.5 else "end" if sdx < -0.5 else "middle"
                canvas.text(
                    parent,
                    x + dx,
                    y + dy + _FONT_MM * 0.35,
                    name,
                    "pin-name",
                    anchor,
                )


def _style(theme: dict[str, str]) -> str:
    t = theme
    return f"""
        .comp-body {{ stroke: {t["body"]}; stroke-width: 1.5; fill: none;
            stroke-linecap: round; stroke-linejoin: round; }}
        .comp-body.fill-bg {{ fill: {t["fill"]}; }}
        .comp-body.fill-solid {{ fill: {t["body"]}; }}
        .component:hover .comp-body {{ stroke: {t["highlight"]}; }}
        .component:hover .comp-label {{ fill: {t["highlight"]}; }}
        .comp-label {{ fill: {t["label"]}; font-family: monospace; font-size: 11px; }}
        .pin-name {{ fill: {t["body"]}; font-family: monospace; font-size: 9px; }}
        .wire {{ stroke: {t["wire"]}; stroke-width: 1.5; stroke-linecap: round; }}
        .wire:hover {{ stroke: {t["highlight"]}; }}
        .node-dot {{ fill: {t["wire"]}; }}
        .ground-symbol .comp-body {{ stroke: {t["body"]}; stroke-width: 1.5; }}
        .net-label {{ fill: {t["net"]}; font-family: monospace; font-size: 11px; }}
        .sim-annotation {{ fill: {t["annotation"]}; font-family: monospace;
            font-size: 10px; }}
        .highlight .comp-body {{ stroke: {t["highlight"]} !important; }}
        .highlight .comp-label {{ fill: {t["highlight"]} !important; }}
        .highlight {{ stroke: {t["highlight"]} !important; }}
        .comp-hit {{ fill: transparent; stroke: none; pointer-events: all; }}
        .wire-hit {{ stroke: transparent !important; stroke-width: 10; }}
    """


def _draw_scene(
    scene: Scene, results: dict | None, theme: dict[str, str]
) -> tuple[_Canvas, ET.Element]:
    canvas = _Canvas()
    style = ET.SubElement(canvas.root, "style")
    style.text = _style(theme)
    background = ET.SubElement(canvas.root, "rect", {"fill": theme["background"]})

    # node -> (position, pin direction) of its first pin
    node_pins: dict[str, tuple[tuple[float, float], int]] = {}
    for pc in scene.placed:
        _draw_component(canvas, scene, pc)
        pins = zip(
            _pin_positions(pc), _pin_directions(pc), pc.component.nodes, strict=False
        )
        for pos, direction, node in pins:
            node_pins.setdefault(node, (pos, direction))

    for wire in scene.wires:
        (x1, y1), (x2, y2) = canvas.px(*wire.start), canvas.px(*wire.end)
        ends = {"x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2)}
        ET.SubElement(
            canvas.root, "line", {**ends, "class": "wire", "data-node": wire.net}
        )
        # Wider invisible copy so the thin wire is easy to hover
        ET.SubElement(
            canvas.root,
            "line",
            {**ends, "class": "wire wire-hit", "data-node": wire.net},
        )

    for jx, jy in scene.junctions:
        cx, cy = canvas.px(jx, jy)
        ET.SubElement(
            canvas.root,
            "circle",
            {"cx": str(cx), "cy": str(cy), "r": str(0.45 * _PX), "class": "node-dot"},
        )

    gnd = scene.lib_symbols.get("power:GND")
    for gx, gy, direction in scene.ground_pins:
        group = ET.SubElement(canvas.root, "g", {"class": "ground-symbol"})
        _draw_symbol(canvas, group, gnd, gx, gy, (direction + 90) % 360)

    for lx, ly, name, direction in scene.net_labels:
        _draw_net_label(canvas, lx, ly, name, direction)

    if results:
        for key, voltage in results.get("nodes", {}).items():
            # Operating point results name node voltages "v(node)"
            match = re.fullmatch(r"v\((.+)\)", key, re.IGNORECASE)
            node = match.group(1).lower() if match else key
            if node in node_pins and isinstance(voltage, (int, float)):
                (nx, ny), direction = node_pins[node]
                _draw_annotation(canvas, nx, ny, f"{voltage:.4g}V", node, direction)
    return canvas, background


def _draw_annotation(
    canvas: _Canvas, x: float, y: float, text: str, node: str, direction: int
) -> None:
    """Simulation value at a pin: outside the component, opposite the net label."""
    gap = 0.3
    if direction == 0:
        pos, anchor = (x + gap, y + gap + _FONT_MM), "start"
    elif direction == 180:
        pos, anchor = (x - gap, y + gap + _FONT_MM), "end"
    elif direction == 90:
        pos, anchor = (x - gap, y - gap), "end"
    else:
        pos, anchor = (x - gap, y + gap + _FONT_MM), "end"
    canvas.text(
        canvas.root, *pos, text, "sim-annotation", anchor, **{"data-node": node}
    )


def _draw_component(canvas: _Canvas, scene: Scene, pc: PlacedComponent) -> None:
    comp = pc.component
    group = ET.SubElement(
        canvas.root,
        "g",
        {
            "id": f"component-{comp.ref}",
            "data-ref": comp.ref,
            "data-type": comp.comp_type,
            "data-value": comp.value,
            "class": "component",
        },
    )
    # Invisible area over the body so hovering works inside open outlines
    x1, y1, x2, y2 = _obstacle_boxes(pc)[0]
    ET.SubElement(
        group,
        "rect",
        {
            "x": str(round(x1 * _PX, 2)),
            "y": str(round(y1 * _PX, 2)),
            "width": str(round((x2 - x1) * _PX, 2)),
            "height": str(round((y2 - y1) * _PX, 2)),
            "class": "comp-hit",
        },
    )
    text = scene.lib_symbols.get(pc.symbol_info.lib_id)
    if text:
        _draw_symbol(canvas, group, text, pc.x, pc.y, pc.rotation)
    (rx, ry), (vx, vy), justify = _field_positions(pc)
    anchor = {"left": "start", "right": "end"}.get(justify, "middle")
    baseline = _FONT_MM * 0.35  # centre the text vertically on its anchor
    canvas.text(group, rx, ry + baseline, comp.ref, "comp-label", anchor)
    if comp.value:
        canvas.text(group, vx, vy + baseline, comp.value, "comp-label", anchor)


def _draw_net_label(
    canvas: _Canvas, x: float, y: float, name: str, direction: int
) -> None:
    """Net label at a pin, running away from the component (see kicad_export)."""
    gap = 0.3
    if direction == 180:
        pos, anchor = (x - gap, y - gap), "end"
    elif direction == 270:
        pos, anchor = (x + gap, y + gap + _FONT_MM), "start"
    else:
        pos, anchor = (x + gap, y - gap), "start"
    canvas.text(canvas.root, *pos, name, "net-label", anchor, **{"data-node": name})


def _finish(
    canvas: _Canvas,
    background: ET.Element,
    width: int | None,
    height: int | None,
) -> str:
    pad = _PADDING_MM * _PX
    min_x, max_x = min(canvas.xs) - pad, max(canvas.xs) + pad
    # Text extends above its baseline
    min_y, max_y = min(canvas.ys) - pad - _FONT_MM * _PX, max(canvas.ys) + pad
    vb_w, vb_h = round(max_x - min_x, 2), round(max_y - min_y, 2)
    min_x, min_y = round(min_x, 2), round(min_y, 2)
    canvas.root.set("viewBox", f"{min_x} {min_y} {vb_w} {vb_h}")
    canvas.root.set("width", str(width if width is not None else vb_w))
    canvas.root.set("height", str(height if height is not None else vb_h))
    # Oversized so it also fills the margins when the viewBox is letterboxed
    background.attrib.update(
        {
            "x": str(round(min_x - vb_w, 2)),
            "y": str(round(min_y - vb_h, 2)),
            "width": str(round(3 * vb_w, 2)),
            "height": str(round(3 * vb_h, 2)),
        }
    )
    return ET.tostring(canvas.root, encoding="unicode", xml_declaration=False)


def _empty_svg(width: int, height: int, theme: dict[str, str]) -> str:
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
    style.text = f".comp-body {{ stroke: {theme['body']}; fill: none; }}"
    ET.SubElement(
        svg,
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": str(width),
            "height": str(height),
            "fill": theme["background"],
        },
    )
    return ET.tostring(svg, encoding="unicode", xml_declaration=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_svg(
    netlist: str,
    results: dict | None = None,
    width: int | None = 800,
    height: int | None = 600,
    theme: str = "dark",
) -> str:
    """Render a SPICE netlist as an interactive SVG string.

    Args:
        netlist: SPICE netlist string.
        results: Optional simulation results dict for overlay annotations.
        width: SVG width in pixels; None uses the drawing's natural size.
        height: SVG height in pixels; None uses the drawing's natural size.
        theme: "dark" (web viewer) or "light" (KiCad colours).

    Returns:
        SVG document as a string.
    """
    colours = _THEMES[theme]
    if not parse_netlist(netlist):
        return _empty_svg(width or 800, height or 600, colours)
    scene = build_scene(netlist)
    if not scene.placed:
        return _empty_svg(width or 800, height or 600, colours)
    canvas, background = _draw_scene(scene, results, colours)
    return _finish(canvas, background, width, height)
