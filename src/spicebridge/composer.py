"""Multi-stage circuit composition engine.

Pure text-processing module — no imports from other spicebridge modules.
Provides port detection, netlist prefixing, and stage composition so that
the AI can build multi-stage designs from templates.
"""

from __future__ import annotations

import re
import warnings

from spicebridge.constants import ANALYSIS_RE, COMPONENT_NODE_COUNTS, END_RE

# Heuristic mappings for auto-detecting port roles
_PORT_HEURISTICS: dict[str, str] = {
    "in": "input",
    "inp": "input",
    "inp1": "input",
    "inp2": "input",
    "in1": "input",
    "in2": "input",
    "in3": "input",
    "out": "output",
    "vout": "output",
    "vcc": "power",
    "vdd": "power",
    "vee": "power",
    "vss": "power",
    "0": "ground",
    "gnd": "ground",
}

_SUBCKT_RE = re.compile(r"^\s*\.subckt\b", re.IGNORECASE)
_ENDS_RE = re.compile(r"^\s*\.ends\b", re.IGNORECASE)
_PARAM_RE = re.compile(r"^\s*\.param\s+(\w+)\s*=\s*(\S+)", re.IGNORECASE)
_INCLUDE_RE = re.compile(r"^\s*\.include\b", re.IGNORECASE)
_COMMENT_RE = re.compile(r"^\s*\*")
_CONT_LINE_RE = re.compile(r"^\s*\+")


# ---------------------------------------------------------------------------
# Helper for auto_detect_ports
# ---------------------------------------------------------------------------


def _extract_nodes_from_line(stripped: str, letter: str, tokens: list[str]) -> set[str]:
    """Extract nodes from a single component instance line.

    Parameters
    ----------
    stripped : str
        The stripped line text.
    letter : str
        The upper-cased first character of the component reference.
    tokens : list[str]
        The whitespace-split tokens of the line.

    Returns
    -------
    set[str]
        The set of node names found on this line.
    """
    nodes: set[str] = set()
    if letter == "X":
        # Subcircuit instance: X<name> node1 node2 ... subckt_name
        # All tokens except first and last are nodes
        if len(tokens) >= 3:
            for tok in tokens[1:-1]:
                nodes.add(tok)
    elif letter in COMPONENT_NODE_COUNTS:
        n_nodes = COMPONENT_NODE_COUNTS[letter]
        for tok in tokens[1 : 1 + n_nodes]:
            nodes.add(tok)
    return nodes


def auto_detect_ports(netlist: str) -> dict[str, str]:
    """Scan a netlist and return a port mapping based on node-name heuristics.

    Returns e.g. ``{"in": "in", "out": "out", "gnd": "0"}``.
    """
    nodes: set[str] = set()
    in_subckt = False
    for line in netlist.splitlines():
        stripped = line.strip()
        if not stripped or _COMMENT_RE.match(stripped):
            continue
        if _SUBCKT_RE.match(stripped):
            in_subckt = True
            continue
        if _ENDS_RE.match(stripped):
            in_subckt = False
            continue
        if in_subckt:
            continue
        if stripped.startswith("."):
            continue
        if _CONT_LINE_RE.match(stripped):
            continue

        tokens = stripped.split()
        if not tokens:
            continue
        ref = tokens[0]
        letter = ref[0].upper()
        nodes |= _extract_nodes_from_line(stripped, letter, tokens)

    ports: dict[str, str] = {}
    for node in nodes:
        lower = node.lower()
        if lower in _PORT_HEURISTICS:
            role = _PORT_HEURISTICS[lower]
            if role == "ground":
                ports["gnd"] = node
            else:
                # Use the node name itself as port name
                ports[node] = node

    return ports


def _rename_node(text: str, old: str, new: str) -> str:
    """Rename a node using word-boundary-aware regex replacement."""
    pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
    return re.sub(pattern, new, text)


# ---------------------------------------------------------------------------
# Helper for prefix_netlist
# ---------------------------------------------------------------------------


def _prefix_component_line(
    tokens: list[str],
    prefix: str,
    preserve_nodes: set[str],
    strip_sources_on: set[str],
    param_names: list[str],
) -> str | None:
    """Handle a component instance line during netlist prefixing.

    Parameters
    ----------
    tokens : list[str]
        The whitespace-split tokens of the line.
    prefix : str
        The prefix to apply.
    preserve_nodes : set[str]
        Nodes that should not be prefixed.
    strip_sources_on : set[str]
        If a V/I source's positive node is in this set, strip it.
    param_names : list[str]
        Parameter names for ``{PARAM}`` reference replacement.

    Returns
    -------
    str or None
        The output line string, or ``None`` if the line should be skipped
        (stripped source).
    """
    ref = tokens[0]
    letter = ref[0].upper()

    if letter == "X":
        # X<name> node1 node2 ... subckt_name
        if len(tokens) < 3:
            return " ".join(tokens)

        # Keep 'X' prefix so ngspice recognises the type
        new_ref = f"X{prefix}_{ref[1:]}"
        node_tokens = tokens[1:-1]
        model_name = tokens[-1]

        new_nodes = []
        for n in node_tokens:
            if n in preserve_nodes:
                new_nodes.append(n)
            else:
                new_nodes.append(f"{prefix}_{n}")
        return f"{new_ref} {' '.join(new_nodes)} {model_name}"

    if letter in COMPONENT_NODE_COUNTS:
        n_nodes = COMPONENT_NODE_COUNTS[letter]

        # Check for source stripping
        if letter in ("V", "I") and len(tokens) >= 2:
            pos_node = tokens[1]
            if pos_node in strip_sources_on:
                return None

        # Keep component letter prefix so ngspice recognises the type
        new_ref = f"{letter}{prefix}_{ref[1:]}"
        new_tokens = [new_ref]

        # Nodes
        for _i, tok in enumerate(tokens[1 : 1 + n_nodes]):
            if tok in preserve_nodes:
                new_tokens.append(tok)
            else:
                new_tokens.append(f"{prefix}_{tok}")

        # Value / remaining tokens
        rest = tokens[1 + n_nodes :]

        # F/H controlled sources: the token after nodes is a V-source name
        if letter in ("F", "H") and rest:
            vref = rest[0]
            if vref[0].upper() == "V":
                rest[0] = f"V{prefix}_{vref[1:]}"
            else:
                rest[0] = f"{prefix}_{vref}"

        new_tokens.extend(rest)
        result_line = " ".join(new_tokens)

        # Replace {PARAM} references with {prefix_PARAM}
        for pname in param_names:
            result_line = result_line.replace(f"{{{pname}}}", f"{{{prefix}_{pname}}}")

        return result_line

    # Unknown component letter — return original line unchanged
    return " ".join(tokens)


def prefix_netlist(
    netlist: str,
    prefix: str,
    preserve_nodes: set[str] | None = None,
    strip_sources_on: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Prefix component references and nodes in a netlist.

    Returns ``(prefixed_netlist_str, list_of_subckt_block_strings)``.

    - ``.subckt``/``.ends`` blocks are extracted verbatim (not prefixed).
    - Analysis directives (``.ac``, ``.tran``, ``.op``, ``.dc``, ``.end``)
      are stripped.
    - ``.param KEY=val`` becomes ``.param {prefix}_KEY=val`` and ``{KEY}``
      references are updated to ``{prefix_KEY}``.
    - ``.include`` lines are kept as-is.
    - Comment lines get the prefix prepended.
    - Component instance lines: ref designator and nodes are prefixed;
      nodes in *preserve_nodes* (always includes ``"0"``) are left alone.
    - V/I sources whose positive node is in *strip_sources_on* are dropped.
    """
    if preserve_nodes is None:
        preserve_nodes = set()
    preserve_nodes = preserve_nodes | {"0"}

    if strip_sources_on is None:
        strip_sources_on = set()

    subckt_blocks: list[str] = []
    param_names: list[str] = []
    out_lines: list[str] = []

    # First pass: collect .subckt blocks and .param names
    lines = netlist.splitlines()
    subckt_buf: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if subckt_buf is not None:
            subckt_buf.append(line)
            if _ENDS_RE.match(stripped):
                subckt_blocks.append("\n".join(subckt_buf))
                subckt_buf = None
            continue
        if _SUBCKT_RE.match(stripped):
            subckt_buf = [line]
            continue

        m = _PARAM_RE.match(stripped)
        if m:
            param_names.append(m.group(1))

    # Second pass: process lines
    subckt_buf = None
    for line in lines:
        stripped = line.strip()

        # Skip subckt blocks (already extracted)
        if subckt_buf is not None:
            if _ENDS_RE.match(stripped):
                subckt_buf = None
            continue
        if _SUBCKT_RE.match(stripped):
            subckt_buf = True  # type: ignore[assignment]
            continue

        # Strip analysis directives
        if ANALYSIS_RE.match(stripped):
            continue
        if END_RE.match(stripped):
            continue

        # .param lines — prefix the key
        m = _PARAM_RE.match(stripped)
        if m:
            key, val = m.group(1), m.group(2)
            out_lines.append(f".param {prefix}_{key}={val}")
            continue

        # .include lines — keep as-is
        if _INCLUDE_RE.match(stripped):
            out_lines.append(line)
            continue

        # Comment lines
        if _COMMENT_RE.match(stripped):
            out_lines.append(f"* [{prefix}] {stripped.lstrip('* ')}")
            continue

        # Blank lines
        if not stripped:
            out_lines.append("")
            continue

        # Continuation lines — skip (rare, simplify)
        if _CONT_LINE_RE.match(stripped):
            continue

        # Dot directives we don't handle — keep as-is
        if stripped.startswith("."):
            out_lines.append(line)
            continue

        # Component instance line
        tokens = stripped.split()
        if not tokens:
            out_lines.append(line)
            continue

        result = _prefix_component_line(
            tokens, prefix, preserve_nodes, strip_sources_on, param_names
        )
        if result is None:
            # Source was stripped
            continue
        out_lines.append(result)

    # Also replace {PARAM} refs in .param value expressions
    final_lines = []
    for line in out_lines:
        updated = line
        for pname in param_names:
            updated = updated.replace(f"{{{pname}}}", f"{{{prefix}_{pname}}}")
        final_lines.append(updated)

    return "\n".join(final_lines), subckt_blocks


# ---------------------------------------------------------------------------
# Helpers for compose_stages
# ---------------------------------------------------------------------------


def _assign_default_labels(stages: list[dict]) -> None:
    """Validate ports and assign default S1/S2/... labels to stages.

    Modifies *stages* in place.  Raises ``ValueError`` if any stage has
    no ports defined.
    """
    for i, stage in enumerate(stages):
        if "label" not in stage or not stage["label"]:
            stage["label"] = f"S{i + 1}"
        if "ports" not in stage or not stage["ports"]:
            raise ValueError(
                f"Stage {i} ('{stage.get('label', '?')}') has no ports defined"
            )


def _auto_build_connections(stages: list[dict]) -> list[dict]:
    """Auto-wire out->in between consecutive stages.

    Returns a list of connection dicts.  Raises ``ValueError`` if an
    output or input port cannot be found for auto-wiring.
    """
    connections: list[dict] = []
    for i in range(len(stages) - 1):
        from_ports = stages[i]["ports"]
        to_ports = stages[i + 1]["ports"]
        # Find output port of from_stage
        from_port = None
        for pname in ("out", "vout", "output"):
            if pname in from_ports:
                from_port = pname
                break
        # Find input port of to_stage
        to_port = None
        for pname in ("in", "inp", "input", "in1"):
            if pname in to_ports:
                to_port = pname
                break
        if from_port is None:
            raise ValueError(
                f"Stage {i} ('{stages[i]['label']}') has no output port "
                f"for auto-wiring. Ports: {list(from_ports.keys())}"
            )
        if to_port is None:
            raise ValueError(
                f"Stage {i + 1} ('{stages[i + 1]['label']}') has no input port "
                f"for auto-wiring. Ports: {list(to_ports.keys())}"
            )
        connections.append(
            {
                "from_stage": i,
                "from_port": from_port,
                "to_stage": i + 1,
                "to_port": to_port,
            }
        )
    return connections


def _validate_connections(connections: list[dict], stages: list[dict]) -> None:
    """Validate connection indices and port names.

    Raises ``ValueError`` on invalid connection.
    """
    for conn in connections:
        fi = conn["from_stage"]
        ti = conn["to_stage"]
        if fi < 0 or fi >= len(stages):
            raise ValueError(f"from_stage {fi} out of range")
        if ti < 0 or ti >= len(stages):
            raise ValueError(f"to_stage {ti} out of range")
        if conn["from_port"] not in stages[fi]["ports"]:
            raise ValueError(f"Port '{conn['from_port']}' not found in stage {fi}")
        if conn["to_port"] not in stages[ti]["ports"]:
            raise ValueError(f"Port '{conn['to_port']}' not found in stage {ti}")


def _process_stages(
    stages: list[dict],
    shared_nodes: set[str],
    incoming_nodes: dict[int, set[str]],
) -> tuple[list[str], list[str], list[str], list[dict]]:
    """Prefix each stage, extract includes, and build stage_infos.

    Returns
    -------
    tuple
        ``(all_subckt_blocks, all_include_lines, stage_netlists, stage_infos)``
    """
    all_subckt_blocks: list[str] = []
    all_include_lines: list[str] = []
    stage_netlists: list[str] = []
    stage_infos: list[dict] = []

    for i, stage in enumerate(stages):
        label = stage["label"]
        preserve = set(shared_nodes)
        strip_on = incoming_nodes[i]

        prefixed, subckt_blocks = prefix_netlist(
            stage["netlist"],
            prefix=label,
            preserve_nodes=preserve,
            strip_sources_on=strip_on,
        )

        all_subckt_blocks.extend(subckt_blocks)

        # Extract .include lines
        filtered_lines = []
        for line in prefixed.splitlines():
            if _INCLUDE_RE.match(line.strip()):
                all_include_lines.append(line.strip())
            else:
                filtered_lines.append(line)

        stage_netlists.append("\n".join(filtered_lines))
        stage_infos.append(
            {
                "label": label,
                "index": i,
                "ports": {
                    pname: (pnode if pnode in shared_nodes else f"{label}_{pnode}")
                    for pname, pnode in stage["ports"].items()
                },
            }
        )

    return all_subckt_blocks, all_include_lines, stage_netlists, stage_infos


def _wire_connections(
    connections: list[dict],
    stages: list[dict],
    stage_netlists: list[str],
    stage_infos: list[dict],
    shared_nodes: set[str],
) -> None:
    """Rename nodes to wire connections between stages.

    Modifies *stage_netlists* and *stage_infos* in place.
    """
    for conn in connections:
        fi = conn["from_stage"]
        ti = conn["to_stage"]
        from_label = stages[fi]["label"]
        to_label = stages[ti]["label"]
        from_node_raw = stages[fi]["ports"][conn["from_port"]]
        to_node_raw = stages[ti]["ports"][conn["to_port"]]

        from_node = (
            from_node_raw
            if from_node_raw in shared_nodes
            else f"{from_label}_{from_node_raw}"
        )
        to_node = (
            to_node_raw if to_node_raw in shared_nodes else f"{to_label}_{to_node_raw}"
        )

        wire_name = f"wire_{from_label}_{to_label}"

        # Rename in all stage netlists
        for j in range(len(stage_netlists)):
            stage_netlists[j] = _rename_node(stage_netlists[j], from_node, wire_name)
            stage_netlists[j] = _rename_node(stage_netlists[j], to_node, wire_name)

        # Update stage_infos port mappings
        for info in stage_infos:
            for pname, pnode in info["ports"].items():
                if pnode in (from_node, to_node):
                    info["ports"][pname] = wire_name


def _deduplicate_subckts(blocks: list[str]) -> list[str]:
    """Deduplicate ``.subckt`` blocks by name.

    Returns a list of unique subckt blocks, keeping the first occurrence
    of each name.  Warns if duplicate names have different content.
    """
    seen_subckts: dict[str, str] = {}
    unique_subckt_blocks: list[str] = []
    for block in blocks:
        first_line = block.strip().splitlines()[0]
        tokens = first_line.split()
        name = tokens[1] if len(tokens) >= 2 else first_line
        if name in seen_subckts:
            if seen_subckts[name].strip() != block.strip():
                warnings.warn(
                    f"Duplicate .subckt '{name}' with different content; "
                    f"keeping first occurrence",
                    stacklevel=2,
                )
        else:
            seen_subckts[name] = block
            unique_subckt_blocks.append(block)
    return unique_subckt_blocks


def compose_stages(
    stages: list[dict],
    connections: list[dict] | None = None,
    shared_ports: list[str] | None = None,
) -> dict:
    """Compose multiple circuit stages into a single netlist.

    Parameters
    ----------
    stages : list of dict
        Each dict has ``"netlist"`` (str), ``"ports"`` (dict), and
        ``"label"`` (str, optional — defaults to ``S1``, ``S2``, ...).
    connections : list of dict, optional
        Each dict: ``{"from_stage": int, "from_port": str,
        "to_stage": int, "to_port": str}``.  If *None*, auto-wires
        ``stages[i].out → stages[i+1].in``.
    shared_ports : list of str, optional
        Port names whose nodes are never prefixed (default ``["gnd"]``).

    Returns
    -------
    dict
        ``{"netlist": str, "ports": dict, "stages": list}``
    """
    if shared_ports is None:
        shared_ports = ["gnd"]

    if not stages:
        raise ValueError("At least one stage is required")

    # Assign default labels
    _assign_default_labels(stages)

    # Build connections
    if connections is None:
        connections = _auto_build_connections(stages)

    # Validate connections
    _validate_connections(connections, stages)

    # Determine which port *nodes* receive incoming connections per stage
    incoming_nodes: dict[int, set[str]] = {i: set() for i in range(len(stages))}
    for conn in connections:
        ti = conn["to_stage"]
        node = stages[ti]["ports"][conn["to_port"]]
        incoming_nodes[ti].add(node)

    # Compute shared port nodes (nodes that are never prefixed)
    shared_nodes: set[str] = {"0"}
    for sp_name in shared_ports:
        for stage in stages:
            if sp_name in stage["ports"]:
                shared_nodes.add(stage["ports"][sp_name])

    # Process each stage
    all_subckt_blocks, all_include_lines, stage_netlists, stage_infos = _process_stages(
        stages, shared_nodes, incoming_nodes
    )

    # Wire connections by renaming nodes
    _wire_connections(connections, stages, stage_netlists, stage_infos, shared_nodes)

    # Deduplicate .subckt blocks by name
    unique_subckt_blocks = _deduplicate_subckts(all_subckt_blocks)

    # Deduplicate .include lines
    unique_includes = list(dict.fromkeys(all_include_lines))

    # Assemble final netlist
    parts: list[str] = []
    parts.append("* Composed multi-stage circuit")

    if unique_subckt_blocks:
        parts.append("")
        for block in unique_subckt_blocks:
            parts.append(block)

    if unique_includes:
        parts.append("")
        for inc in unique_includes:
            parts.append(inc)

    for i, sn in enumerate(stage_netlists):
        parts.append("")
        parts.append(f"* --- Stage: {stages[i]['label']} ---")
        parts.append(sn)

    combined = "\n".join(parts)

    # Build combined ports: input from first stage, output from last stage
    combined_ports: dict[str, str] = {}
    first_info = stage_infos[0]
    last_info = stage_infos[-1]

    # Input ports from first stage
    for pname in ("in", "inp", "in1", "inp1", "inp2", "in2", "in3"):
        if pname in first_info["ports"]:
            combined_ports[pname] = first_info["ports"][pname]

    # Output ports from last stage
    for pname in ("out", "vout"):
        if pname in last_info["ports"]:
            combined_ports[pname] = last_info["ports"][pname]

    # Ground
    combined_ports["gnd"] = "0"

    return {
        "netlist": combined,
        "ports": combined_ports,
        "stages": stage_infos,
    }
