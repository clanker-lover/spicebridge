"""SPICE netlist parser and schematic drawing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from spicebridge.constants import COMPONENT_NODE_COUNTS


@dataclass
class ParsedComponent:
    """A single component extracted from a SPICE netlist."""

    comp_type: str  # Single letter: R, C, L, V, I, D, Q, M, X
    ref: str  # e.g. "R1", "C2"
    nodes: list[str] = field(default_factory=list)
    value: str = ""  # Value/model string, e.g. "1k", "AC 1"


# Ground node aliases (lowercase)
_GROUND_NAMES = {"0", "gnd", "gnd!"}


def _is_ground(node: str) -> bool:
    """Check if a node name represents ground."""
    return node.lower() in _GROUND_NAMES


# Keywords of the behavioral E/G forms (PSpice/ngspice) when written as a
# separate token, e.g. "VALUE = {...}" or "TABLE {V(a)} = ..."
_BEHAVIORAL_KEYWORDS = {"value", "table", "laplace", "freq", "vol", "cur"}


def _is_behavioral(tokens: list[str]) -> bool:
    """Return True for an E/G line in a behavioral form instead of 4 nodes.

    E.g. ``E1 out 0 VALUE={V(a)*2}``, ``E1 out 0 POLY(1) a 0 0 2`` or
    ``G1 out 0 cur='V(a)*1m'``: the token after the two output nodes is a
    keyword or an expression, not a node name. Node names never contain
    ``=``, ``{`` or quotes, so a bare keyword only counts when an expression
    follows it.
    """
    if len(tokens) < 4:
        return False
    tok = tokens[3].lower()
    if tok.startswith("poly(") or any(c in tok for c in "={'"):
        return True
    return tok in _BEHAVIORAL_KEYWORDS and len(tokens) > 4 and tokens[4][0] in "={'"


def _split_params(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split X-line tokens into (nodes + subcircuit name, parameters)."""
    for i, tok in enumerate(tokens):
        if tok.lower() == "params:" or "=" in tok:
            return tokens[:i], tokens[i:]
    return tokens, []


def parse_netlist(netlist: str) -> list[ParsedComponent]:
    """Parse a SPICE netlist into a list of ParsedComponent objects.

    Per SPICE convention the first line is the title and is always ignored
    (this matches ngspice and the title handling in ``create_circuit``).
    Also skips blank lines, comments (*), directives (.), continuations (+)
    and the bodies of ``.subckt ... .ends`` definitions.
    """
    components: list[ParsedComponent] = []
    subckt_depth = 0

    for line in netlist.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("*"):
            continue
        lowered = stripped.lower()
        if lowered.startswith(".subckt"):
            subckt_depth += 1
            continue
        if lowered.startswith(".ends"):
            subckt_depth = max(0, subckt_depth - 1)
            continue
        if subckt_depth:
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
            # Subcircuit: nodes are between ref and the subcircuit name,
            # optionally followed by parameters ("params: R=1k" or "R=1k")
            body, params = _split_params(tokens[1:])
            if len(body) >= 2:
                nodes = [n.lower() for n in body[:-1]]
                value = " ".join([body[-1], *params])
            else:
                nodes = []
                value = " ".join(body + params)
        elif comp_type in ("E", "G") and _is_behavioral(tokens):
            # Behavioral form (VALUE=, POLY(n), TABLE, ...): output nodes only
            nodes = [n.lower() for n in tokens[1:3]]
            value = " ".join(tokens[3:])
        elif comp_type in COMPONENT_NODE_COUNTS:
            n_nodes = COMPONENT_NODE_COUNTS[comp_type]
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


def draw_schematic(netlist: str, output_path: str | Path, fmt: str = "png") -> Path:
    """Draw a schematic from a SPICE netlist and save to file.

    Uses the same layout and symbols as the KiCad export (via
    ``svg_renderer.render_svg``) in KiCad's colours; PNG is rendered from
    the SVG with cairosvg.

    Args:
        netlist: SPICE netlist string.
        output_path: Path to save the output image.
        fmt: Output format ('png' or 'svg').

    Returns:
        Path to the saved schematic file.

    Raises:
        ValueError: If the netlist contains no components.
    """
    # Imported here: svg_renderer depends on this module
    from spicebridge.svg_renderer import render_svg

    if not parse_netlist(netlist):
        raise ValueError("Netlist contains no components to draw")

    output_path = Path(output_path)
    svg = render_svg(netlist, width=None, height=None, theme="light")
    if fmt == "svg":
        output_path.write_text(svg, encoding="utf-8")
    else:
        import cairosvg

        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(output_path))
    return output_path
