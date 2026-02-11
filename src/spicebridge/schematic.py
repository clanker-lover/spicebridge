"""Netlist-to-schemdraw schematic generator."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import schemdraw  # noqa: E402
import schemdraw.elements as elm  # noqa: E402


@dataclass
class ParsedComponent:
    """A single component extracted from a SPICE netlist."""

    comp_type: str  # Single letter: R, C, L, V, I, D, Q, M, X
    ref: str  # e.g. "R1", "C2"
    nodes: list[str] = field(default_factory=list)
    value: str = ""  # Value/model string, e.g. "1k", "AC 1"


# Node counts per component type
_NODE_COUNTS: dict[str, int] = {
    "R": 2,
    "C": 2,
    "L": 2,
    "V": 2,
    "I": 2,
    "D": 2,
    "Q": 3,
    "M": 4,
}

# Ground node aliases (lowercase)
_GROUND_NAMES = {"0", "gnd", "gnd!"}


def _is_ground(node: str) -> bool:
    """Check if a node name represents ground."""
    return node.lower() in _GROUND_NAMES


def parse_netlist(netlist: str) -> list[ParsedComponent]:
    """Parse a SPICE netlist into a list of ParsedComponent objects.

    Skips blank lines, comments (*), directives (.), and continuations (+).
    """
    components: list[ParsedComponent] = []

    for line in netlist.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            continue
        if stripped.startswith("."):
            continue
        if stripped.startswith("+"):
            continue

        tokens = stripped.split()
        if not tokens:
            continue

        ref = tokens[0]
        comp_type = ref[0].upper()

        if comp_type == "X":
            # Subcircuit: nodes are between ref and last token (model name)
            if len(tokens) >= 3:
                nodes = [n.lower() for n in tokens[1:-1]]
                value = tokens[-1]
            else:
                nodes = []
                value = ""
        elif comp_type in _NODE_COUNTS:
            n_nodes = _NODE_COUNTS[comp_type]
            nodes = [n.lower() for n in tokens[1 : 1 + n_nodes]]
            value = " ".join(tokens[1 + n_nodes :])
        else:
            # Unknown component type — treat as 2-node
            nodes = [n.lower() for n in tokens[1:3]] if len(tokens) >= 3 else []
            value = " ".join(tokens[3:]) if len(tokens) > 3 else ""

        components.append(
            ParsedComponent(
                comp_type=comp_type,
                ref=ref,
                nodes=nodes,
                value=value,
            )
        )

    return components


# Mapping from component type to schemdraw element class
_ELEMENT_MAP: dict[str, type] = {
    "R": elm.Resistor,
    "C": elm.Capacitor,
    "L": elm.Inductor,
    "V": elm.SourceV,
    "I": elm.SourceI,
    "D": elm.Diode,
    "Q": elm.BjtNpn,
    "M": elm.NFet,
    "X": elm.Opamp,
}


def _is_ac_source(comp: ParsedComponent) -> bool:
    """Check if a voltage source has AC specification."""
    return bool(re.search(r"\bac\b", comp.value, re.IGNORECASE))


def _classify_components(
    components: list[ParsedComponent],
) -> tuple[list[ParsedComponent], list[ParsedComponent], list[ParsedComponent]]:
    """Split components into sources, series, and shunt lists.

    Sources are voltage (V) or current (I) sources.
    Shunt components have at least one ground-connected node.
    Series components are everything else.

    Returns:
        Tuple of (sources, series, shunt).
    """
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

    return sources, series, shunt


def _draw_sources(
    d: schemdraw.Drawing,
    sources: list[ParsedComponent],
    node_positions: dict[str, tuple[float, float]],
) -> None:
    """Draw source elements on the drawing going UP from ground.

    Updates node_positions with the start/end positions of each source.
    """
    for comp in sources:
        elem_cls = (
            elm.SourceSin if _is_ac_source(comp) else _ELEMENT_MAP[comp.comp_type]
        )
        e = d.add(elem_cls().up().label(comp.ref))
        if comp.nodes:
            node_positions[comp.nodes[0]] = e.end
            if len(comp.nodes) > 1:
                node_positions[comp.nodes[1]] = e.start


def _draw_series(
    d: schemdraw.Drawing,
    series: list[ParsedComponent],
    node_positions: dict[str, tuple[float, float]],
) -> None:
    """Draw series elements going RIGHT.

    Updates node_positions with the start/end positions of each element.
    """
    for comp in series:
        elem_cls = _ELEMENT_MAP.get(comp.comp_type, elm.Resistor)
        e = d.add(elem_cls().right().label(comp.ref))
        if comp.nodes:
            node_positions[comp.nodes[0]] = e.start
            if len(comp.nodes) > 1:
                node_positions[comp.nodes[1]] = e.end


def _draw_shunt(
    d: schemdraw.Drawing,
    shunt: list[ParsedComponent],
    node_positions: dict[str, tuple[float, float]],
) -> None:
    """Draw shunt-to-ground elements going DOWN.

    For each shunt component, if its signal node (non-ground) already has a
    known position, a short line is drawn from that position before placing
    the component downward with a ground symbol underneath.
    """
    for comp in shunt:
        elem_cls = _ELEMENT_MAP.get(comp.comp_type, elm.Resistor)
        # Find the signal node (non-ground)
        signal_node = None
        for n in comp.nodes:
            if not _is_ground(n):
                signal_node = n
                break

        if signal_node and signal_node in node_positions:
            d.add(elm.Line().at(node_positions[signal_node]).down().length(0.5))

        d.add(elem_cls().down().label(comp.ref))
        d.add(elm.Ground())


def _draw_source_grounds(
    d: schemdraw.Drawing,
    sources: list[ParsedComponent],
    node_positions: dict[str, tuple[float, float]],
) -> None:
    """Add ground symbols under sources.

    Finds the first source with a ground node that has a known position
    and places a ground symbol there.
    """
    if not sources:
        return

    first_source_gnd = None
    for comp in sources:
        for n in comp.nodes:
            if _is_ground(n) and n in node_positions:
                first_source_gnd = node_positions[n]
                break
        if first_source_gnd:
            break
    if first_source_gnd:
        d.add(elm.Ground().at(first_source_gnd))


def draw_schematic(netlist: str, output_path: str | Path, fmt: str = "png") -> Path:
    """Draw a schematic from a SPICE netlist and save to file.

    Args:
        netlist: SPICE netlist string.
        output_path: Path to save the output image.
        fmt: Output format ('png' or 'svg').

    Returns:
        Path to the saved schematic file.

    Raises:
        ValueError: If the netlist contains no components.
    """
    components = parse_netlist(netlist)
    if not components:
        raise ValueError("Netlist contains no components to draw")

    output_path = Path(output_path)

    sources, series, shunt = _classify_components(components)

    d = schemdraw.Drawing(show=False)

    # Track node positions for connecting shunt components
    node_positions: dict[str, tuple[float, float]] = {}

    # 1. Draw sources on the left going UP from ground
    _draw_sources(d, sources, node_positions)

    # 2. Draw series components going RIGHT
    _draw_series(d, series, node_positions)

    # 3. Draw shunt components going DOWN to ground
    _draw_shunt(d, shunt, node_positions)

    # Add a ground at the source bottom if we drew sources
    _draw_source_grounds(d, sources, node_positions)

    d.save(str(output_path))
    return output_path
