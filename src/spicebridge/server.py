"""MCP server exposing SPICEBridge tools for AI clients."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent, ToolAnnotations
from starlette.responses import Response

from spicebridge.circuit_manager import CircuitManager
from spicebridge.composer import auto_detect_ports, compose_stages
from spicebridge.kicad_export import export_kicad_schematic as _export_kicad
from spicebridge.model_generator import generate_model
from spicebridge.model_store import ModelStore
from spicebridge.monte_carlo import (
    apply_corner,
    build_analysis_cmd,
    compute_sensitivity,
    compute_statistics,
    compute_worst_case,
    generate_corners,
    parse_component_values,
    randomize_values,
    run_single_sim,
    substitute_values,
)
from spicebridge.netlist_utils import prepare_netlist as _prepare_netlist
from spicebridge.parser import (
    parse_results,
    read_ac_at_frequency,
    read_ac_bandwidth,
)
from spicebridge.sanitize import (
    safe_error_response,
    safe_path,
    sanitize_netlist,
    validate_component_value,
    validate_filename,
    validate_format,
    validate_include_paths,
)
from spicebridge.schematic import draw_schematic as _draw_schematic
from spicebridge.schematic import parse_netlist
from spicebridge.schematic_cache import SchematicCache
from spicebridge.simulator import run_simulation, validate_netlist_syntax
from spicebridge.solver import solve as _solve_components
from spicebridge.standard_values import (
    format_engineering,
    parse_spice_value,
    snap_to_standard,
)
from spicebridge.template_manager import (
    TemplateManager,
    modify_component_in_netlist,
    substitute_params,
)
from spicebridge.web_viewer import get_viewer_server, start_viewer

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "SPICEBridge",
    instructions=(
        "SPICEBridge is a circuit design tool. When any tool response contains a "
        "schematic_url field, you MUST include that URL as a clickable link in your "
        "message to the user. This is a public internet-accessible URL hosted at "
        "mcp.clanker-lover.work — it is NOT a sandbox, localhost, or internal URL. "
        "The user CANNOT see inline images or tool result renders. The schematic_url "
        "is the ONLY way the user can view their schematic."
    ),
)
_manager = CircuitManager()
_templates = TemplateManager()
_models = ModelStore()
_schematic_cache = SchematicCache()

if not shutil.which("ngspice"):
    logger.warning(
        "ngspice not found on PATH. Simulation tools will fail. "
        "Install with: sudo apt install ngspice"
    )

# Validation patterns for user-supplied names
_PORT_NAME_RE = re.compile(r"^[A-Za-z0-9_.$#-]+$")
_STAGE_LABEL_RE = re.compile(r"^[A-Za-z0-9_]+$")

# --- Resource limits ---
_MAX_NETLIST_SIZE = 100_000  # 100 KB
_MAX_STAGES = 20
_MAX_MONTE_CARLO_RUNS = 100
_MONTE_CARLO_TIMEOUT = 30 * 60  # 30 minutes
_MAX_WORST_CASE_COMPONENTS = 20
_MAX_WORST_CASE_SIMS = 500


def _resolve_model_includes(model_names: list[str]) -> str:
    """Return .include lines for the given model names, or raise ValueError."""
    lines = []
    for name in model_names:
        try:
            lib_path = _models.get_lib_path(name)
            lines.append(f".include {lib_path}")
        except KeyError:
            raise ValueError(
                f"Model '{name}' not found. Use list_models() to see available models."
            ) from None
    return "\n".join(lines)


_http_transport: bool = False


def _get_base_url() -> str | None:
    """Return the configured base URL for serving schematics, or None."""
    url = os.environ.get("SPICEBRIDGE_BASE_URL", "").rstrip("/")
    return url or None


def _schematic_url(circuit_id: str) -> str | None:
    """Return the public URL for a cached schematic PNG, or None."""
    base = _get_base_url()
    return f"{base}/schematics/{circuit_id}.png" if base else None


def _svg_to_image_content(
    svg_content: str, circuit_id: str | None = None
) -> ImageContent:
    """Convert SVG to PNG and return an MCP ImageContent block."""
    import cairosvg

    png_bytes = cairosvg.svg2png(bytestring=svg_content.encode("utf-8"))
    if circuit_id is not None:
        _schematic_cache.put(circuit_id, png_bytes)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return ImageContent(type="image", data=b64, mimeType="image/png")


def _error_content(error_dict: dict) -> list[TextContent]:
    """Wrap an error dict as a list containing a single TextContent block."""
    return [TextContent(type="text", text=json.dumps(error_dict))]


_favicon_png: bytes | None = None


def _load_favicon() -> bytes | None:
    """Load assets/logo.svg and convert to PNG for favicon use."""
    global _favicon_png
    if _favicon_png is not None:
        return _favicon_png
    svg_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.svg"
    if not svg_path.is_file():
        return None
    import cairosvg

    _favicon_png = cairosvg.svg2png(
        url=str(svg_path), output_width=64, output_height=64
    )
    return _favicon_png


@mcp.custom_route("/favicon.ico", methods=["GET"])
async def serve_favicon(request):
    """Serve the SPICEBridge logo as a favicon."""
    png = _load_favicon()
    if png is None:
        return Response(content=b"", status_code=404)
    return Response(content=png, status_code=200, media_type="image/png")


@mcp.custom_route("/schematics/{circuit_id}.png", methods=["GET"])
async def serve_schematic(request):
    """Serve a cached schematic PNG over HTTP."""
    circuit_id = request.path_params["circuit_id"]
    png_bytes = _schematic_cache.get(circuit_id)
    if png_bytes is None:
        return Response(
            content='{"error": "Schematic not found"}',
            status_code=404,
            media_type="application/json",
        )
    return Response(content=png_bytes, status_code=200, media_type="image/png")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Circuit",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def create_circuit(netlist: str, models: list[str] | None = None) -> dict:
    """Store a SPICE netlist and return a circuit ID for subsequent analyses."""
    if len(netlist) > _MAX_NETLIST_SIZE:
        return {
            "status": "error",
            "error": (
                f"Netlist too large ({len(netlist)} bytes); "
                f"limit is {_MAX_NETLIST_SIZE}"
            ),
        }
    try:
        sanitize_netlist(netlist)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if models:
        try:
            includes = _resolve_model_includes(models)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        # Insert .include lines after the title (first line) per SPICE convention
        lines = netlist.split("\n", 1)
        if len(lines) == 2:
            netlist = lines[0] + "\n" + includes + "\n" + lines[1]
        else:
            netlist = netlist + "\n" + includes
    circuit_id = _manager.create(netlist)
    try:
        detected = auto_detect_ports(netlist)
        if detected:
            _manager.set_ports(circuit_id, detected)
    except (ValueError, KeyError, IndexError):
        logger.debug("Port auto-detection failed", exc_info=True)
    viewer = get_viewer_server()
    if viewer is not None:
        viewer.notify_change({"type": "circuit_created", "circuit_id": circuit_id})
    preview_lines = netlist.strip().splitlines()[:5]
    return {
        "status": "ok",
        "circuit_id": circuit_id,
        "preview": preview_lines,
        "num_lines": len(netlist.strip().splitlines()),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run AC Analysis",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def run_ac_analysis(
    circuit_id: str,
    start_freq: float = 1.0,
    stop_freq: float = 1e6,
    points_per_decade: int = 10,
) -> dict:
    """Run AC analysis on a stored circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    try:
        points_per_decade = int(points_per_decade)
        start_freq = float(start_freq)
        stop_freq = float(stop_freq)
    except (ValueError, TypeError) as exc:
        return {"status": "error", "error": f"Invalid analysis parameter: {exc}"}

    if not 1 <= points_per_decade <= 1000:
        return {
            "status": "error",
            "error": "points_per_decade must be between 1 and 1000",
        }
    if start_freq <= 0:
        return {"status": "error", "error": "start_freq must be > 0"}
    if stop_freq <= start_freq:
        return {"status": "error", "error": "stop_freq must be > start_freq"}

    analysis_line = f".ac dec {points_per_decade} {start_freq} {stop_freq}"
    prepared = _prepare_netlist(circuit.netlist, analysis_line)

    try:
        success = run_simulation(prepared, output_dir=circuit.output_dir)
        if not success:
            return {"status": "error", "error": "Simulation produced no output"}
        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        _manager.update_results(circuit_id, results)
        viewer = get_viewer_server()
        if viewer is not None:
            viewer.notify_change({"type": "results_updated", "circuit_id": circuit_id})
        return {"status": "ok", "results": results}
    except Exception as e:
        return safe_error_response(e, logger, "run_ac_analysis")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run Transient Analysis",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def run_transient(
    circuit_id: str,
    stop_time: float,
    step_time: float,
    startup_time: float | None = None,
) -> dict:
    """Run transient analysis on a stored circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    try:
        stop_time = float(stop_time)
        step_time = float(step_time)
        if startup_time is not None:
            startup_time = float(startup_time)
    except (ValueError, TypeError) as exc:
        return {"status": "error", "error": f"Invalid analysis parameter: {exc}"}

    if step_time <= 0:
        return {"status": "error", "error": "step_time must be > 0"}
    if stop_time <= 0:
        return {"status": "error", "error": "stop_time must be > 0"}
    if stop_time / step_time > 1_000_000:
        return {
            "status": "error",
            "error": "stop_time/step_time exceeds 1,000,000 steps",
        }

    if startup_time is not None:
        analysis_line = f".tran {step_time} {stop_time} {startup_time}"
    else:
        analysis_line = f".tran {step_time} {stop_time}"

    prepared = _prepare_netlist(circuit.netlist, analysis_line)

    try:
        success = run_simulation(prepared, output_dir=circuit.output_dir)
        if not success:
            return {"status": "error", "error": "Simulation produced no output"}
        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        _manager.update_results(circuit_id, results)
        viewer = get_viewer_server()
        if viewer is not None:
            viewer.notify_change({"type": "results_updated", "circuit_id": circuit_id})
        return {"status": "ok", "results": results}
    except Exception as e:
        return safe_error_response(e, logger, "run_transient")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run DC Operating Point",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def run_dc_op(circuit_id: str) -> dict:
    """Run DC operating point analysis on a stored circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    prepared = _prepare_netlist(circuit.netlist, ".op")

    try:
        success = run_simulation(prepared, output_dir=circuit.output_dir)
        if not success:
            return {"status": "error", "error": "Simulation produced no output"}
        raw_path = circuit.output_dir / "circuit.raw"
        results = parse_results(raw_path)
        _manager.update_results(circuit_id, results)
        viewer = get_viewer_server()
        if viewer is not None:
            viewer.notify_change({"type": "results_updated", "circuit_id": circuit_id})
        return {"status": "ok", "results": results}
    except Exception as e:
        return safe_error_response(e, logger, "run_dc_op")


def _summarize_results(data: object) -> object:
    """Recursively cap large lists in results to first/last 5 elements."""
    if isinstance(data, list):
        if len(data) > 50:
            return {
                "type": "array",
                "length": len(data),
                "first_5": [_summarize_results(x) for x in data[:5]],
                "last_5": [_summarize_results(x) for x in data[-5:]],
            }
        return [_summarize_results(x) for x in data]
    if isinstance(data, dict):
        return {k: _summarize_results(v) for k, v in data.items()}
    return data


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Results",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def get_results(circuit_id: str, summary: bool = True) -> dict:
    """Return the last simulation results for a circuit."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    results = circuit.last_results
    if summary and results is not None:
        results = _summarize_results(results)

    return {
        "status": "ok",
        "results": results,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Circuit",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def delete_circuit(circuit_id: str) -> dict:
    """Delete a stored circuit and clean up its output directory."""
    try:
        _manager.delete(circuit_id)
    except KeyError:
        return {"status": "error", "error": f"Circuit '{circuit_id}' not found"}
    _schematic_cache.delete(circuit_id)
    return {"status": "ok"}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Draw Schematic",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def draw_schematic(circuit_id: str, fmt: str = "png") -> list:
    """Generate a schematic diagram from a stored circuit's netlist.

    When a schematic_url is present in the response, ALWAYS include it as a
    clickable link in your message to the user. Users cannot see inline images.
    The schematic_url in the response is a public internet-accessible URL, not a
    sandbox or localhost URL. Always share it directly with the user."""
    try:
        validate_format(fmt)
    except ValueError as e:
        return _error_content({"status": "error", "error": str(e)})
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return _error_content({"status": "error", "error": str(e)})

    try:
        output_file = safe_path(circuit.output_dir, f"schematic.{fmt}")
        _draw_schematic(circuit.netlist, output_file, fmt=fmt)
        # Always provide SVG content for cloud mode (MCP over streamable-http)
        if fmt == "svg":
            svg_content = output_file.read_text(encoding="utf-8")
        else:
            svg_file = safe_path(circuit.output_dir, "schematic.svg")
            _draw_schematic(circuit.netlist, svg_file, fmt="svg")
            svg_content = svg_file.read_text(encoding="utf-8")
        metadata = {
            "status": "ok",
            "filepath": output_file.name,
            "format": fmt,
            "svg_content": svg_content,
        }
        url = _schematic_url(circuit_id)
        if url:
            metadata["schematic_url"] = url
            metadata["_assistant_hint"] = (
                f"IMPORTANT: ALWAYS share this URL with the user in your response. Inline images are NOT visible to them in Claude.ai. The user can ONLY see the schematic if you include this link. This is a PUBLIC URL accessible from any browser, not a sandbox or internal URL: {url}"
            )
        if _http_transport:
            metadata.pop("svg_content", None)
            metadata.pop("filepath", None)
        blocks = [TextContent(type="text", text=json.dumps(metadata))]
        if _http_transport and url:
            blocks.append(
                TextContent(
                    type="text",
                    text=f"INSTRUCTION: The user cannot see inline images. You MUST include this schematic link in your response. This is a PUBLIC URL accessible from any browser, not a sandbox or internal URL: {url}",
                )
            )
        if not _http_transport:
            blocks.append(_svg_to_image_content(svg_content, circuit_id=circuit_id))
        return blocks
    except Exception as e:
        return _error_content(safe_error_response(e, logger, "draw_schematic"))


@mcp.tool(
    annotations=ToolAnnotations(
        title="Export KiCad Schematic",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def export_kicad(circuit_id: str, filename: str | None = None) -> dict:
    """Export circuit as KiCad 8 schematic (.kicad_sch) file."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}
    try:
        fname = filename or f"{circuit_id}.kicad_sch"
        validate_filename(fname)
        output_path, warnings = _export_kicad(
            circuit.netlist,
            output_dir=circuit.output_dir,
            filename=fname,
        )
        kicad_content = output_path.read_text(encoding="utf-8")
        comps = parse_netlist(circuit.netlist)
        return {
            "status": "ok",
            "file_path": output_path.name,
            "num_components": len(comps),
            "warnings": warnings,
            "kicad_content": kicad_content,
        }
    except Exception as e:
        return safe_error_response(e, logger, "export_kicad")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Open Schematic Viewer",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def open_viewer(circuit_id: str | None = None, port: int = 8080) -> dict:
    """Start the interactive web schematic viewer and return its URL."""
    if not (1024 <= port <= 65535):
        return {"error": "Port must be between 1024 and 65535"}
    try:
        url = start_viewer(_manager, port=port)
    except Exception as e:
        return safe_error_response(e, logger, "open_viewer")
    result: dict = {"status": "ok", "url": url, "port": port}
    viewer = get_viewer_server()
    if viewer is not None:
        token = viewer._auth_token
        result["auth_token"] = token
        result["authenticated_url"] = f"{url}?token={token}"
    if circuit_id is not None:
        result["circuit_id"] = circuit_id
        result["hint"] = f"Open {url}#circuit={circuit_id} to view this circuit"
    return result


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Ports",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def set_ports(circuit_id: str, ports: dict) -> dict:
    """Store port definitions for a circuit.

    *ports* maps port names to netlist node names,
    e.g. ``{"in": "in", "out": "out", "gnd": "0"}``.
    """
    try:
        _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    for key, val in ports.items():
        for label, name in [("port name", key), ("node name", val)]:
            if not isinstance(name, str) or not _PORT_NAME_RE.match(name):
                return {
                    "status": "error",
                    "error": f"Invalid {label} '{name}': must match [A-Za-z0-9_.$#-]+",
                }

    _manager.set_ports(circuit_id, ports)
    return {"status": "ok", "circuit_id": circuit_id, "ports": ports}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Ports",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def get_ports(circuit_id: str) -> dict:
    """Return port definitions for a circuit, auto-detecting if none are set."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    ports = _manager.get_ports(circuit_id)
    if ports is None:
        try:
            ports = auto_detect_ports(circuit.netlist)
        except (ValueError, KeyError, IndexError):
            ports = {}
    return {"status": "ok", "circuit_id": circuit_id, "ports": ports}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Connect Stages",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def connect_stages(
    stages: list[dict],
    connections: list[dict] | None = None,
    shared_ports: list[str] | None = None,
) -> dict:
    """Compose multiple circuit stages into a single combined circuit.

    Each element of *stages* is ``{"circuit_id": str, "label": str (optional)}``.
    If *connections* is omitted, stages are auto-wired in order
    (out of stage N → in of stage N+1).
    *shared_ports* defaults to ``["gnd"]`` — those nodes are never prefixed.
    """
    if len(stages) > _MAX_STAGES:
        return {
            "status": "error",
            "error": f"Too many stages ({len(stages)}); limit is {_MAX_STAGES}",
        }
    resolved: list[dict] = []
    for i, s in enumerate(stages):
        cid = s.get("circuit_id")
        if not cid:
            return {
                "status": "error",
                "error": f"Stage {i} missing 'circuit_id'",
            }
        try:
            circuit = _manager.get(cid)
        except KeyError as e:
            return {"status": "error", "error": str(e)}

        ports = _manager.get_ports(cid)
        if ports is None:
            try:
                ports = auto_detect_ports(circuit.netlist)
            except (ValueError, KeyError, IndexError):
                ports = {}
        if not ports:
            return {
                "status": "error",
                "error": f"Stage {i} (circuit '{cid}') has no ports",
            }

        label = s.get("label", "")
        if label and not _STAGE_LABEL_RE.match(label):
            return {
                "status": "error",
                "error": f"Invalid stage label '{label}': must match [A-Za-z0-9_]+",
            }

        resolved.append(
            {
                "netlist": circuit.netlist,
                "ports": ports,
                "label": label,
            }
        )

    try:
        result = compose_stages(resolved, connections, shared_ports)
    except (ValueError, KeyError) as e:
        return {"status": "error", "error": str(e)}

    try:
        sanitize_netlist(result["netlist"], _allow_includes=True)
        validate_include_paths(result["netlist"], [_models.base_dir])
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    new_id = _manager.create(result["netlist"])
    _manager.set_ports(new_id, result["ports"])

    preview = result["netlist"].strip().splitlines()[:10]
    return {
        "status": "ok",
        "circuit_id": new_id,
        "num_stages": len(stages),
        "stages": result["stages"],
        "netlist_preview": preview,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Templates",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def list_templates(category: str | None = None) -> dict:
    """List available circuit templates, optionally filtered by category."""
    templates = _templates.list_templates(category=category)
    return {"status": "ok", "templates": templates, "count": len(templates)}


@mcp.tool(
    annotations=ToolAnnotations(
        title="Load Template",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def load_template(
    template_id: str,
    params: dict | None = None,
    specs: dict | None = None,
    models: list[str] | None = None,
) -> dict:
    """Load a circuit template and create a circuit from it.

    If *specs* is provided, the design-equation solver runs automatically,
    results are snapped to E24 standard values, and the netlist `.param`
    lines are updated.  Explicit *params* override solver-calculated values.
    """
    try:
        t = _templates.get_template(template_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    netlist = t.netlist
    calculated_values: dict[str, str] | None = None
    solver_notes: list[str] | None = None

    if specs is not None:
        try:
            netlist, calculated_values, solver_notes, params = _solve_and_snap(
                template_id, specs, params, netlist
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

    if params:
        try:
            for v in params.values():
                validate_component_value(str(v))
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        netlist = substitute_params(netlist, params)

    if models:
        try:
            includes = _resolve_model_includes(models)
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        # Insert .include lines after the title (first line) per SPICE convention
        lines = netlist.split("\n", 1)
        if len(lines) == 2:
            netlist = lines[0] + "\n" + includes + "\n" + lines[1]
        else:
            netlist = netlist + "\n" + includes

    try:
        sanitize_netlist(netlist, _allow_includes=True)
        validate_include_paths(netlist, [_models.base_dir])
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    circuit_id = _manager.create(netlist)
    # Store ports from template or auto-detect
    if t.ports:
        _manager.set_ports(circuit_id, t.ports)
    else:
        try:
            detected = auto_detect_ports(netlist)
            if detected:
                _manager.set_ports(circuit_id, detected)
        except (ValueError, KeyError, IndexError):
            logger.debug("Port auto-detection failed", exc_info=True)
    preview_lines = netlist.strip().splitlines()[:5]
    result = {
        "status": "ok",
        "circuit_id": circuit_id,
        "preview": preview_lines,
        "num_lines": len(netlist.strip().splitlines()),
        "components": t.components,
        "design_equations": t.design_equations,
    }
    if calculated_values is not None:
        result["calculated_values"] = calculated_values
    if solver_notes is not None:
        result["solver_notes"] = solver_notes
    return result


@mcp.tool(
    annotations=ToolAnnotations(
        title="Modify Component",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def modify_component(circuit_id: str, component: str, value: str) -> dict:
    """Modify a component value in a stored circuit's netlist."""
    try:
        validate_component_value(value)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    try:
        new_netlist = modify_component_in_netlist(circuit.netlist, component, value)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    _manager.update_netlist(circuit_id, new_netlist)
    viewer = get_viewer_server()
    if viewer is not None:
        viewer.notify_change({"type": "circuit_updated", "circuit_id": circuit_id})
    preview_lines = new_netlist.strip().splitlines()[:5]
    return {
        "status": "ok",
        "circuit_id": circuit_id,
        "preview": preview_lines,
        "num_lines": len(new_netlist.strip().splitlines()),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Validate Netlist",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def validate_netlist(circuit_id: str) -> dict:
    """Validate the netlist syntax of a stored circuit using ngspice."""
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    prepared = _prepare_netlist(circuit.netlist, ".op")
    try:
        valid, errors = validate_netlist_syntax(prepared)
    except RuntimeError as e:
        return {"status": "error", "error": str(e)}

    return {"status": "ok", "valid": valid, "errors": errors}


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _require_results(circuit_id: str, analysis_type: str) -> tuple | dict:
    """Validate circuit exists, has results, and results match analysis type.

    Returns (circuit, results) on success, or an error dict on failure.
    """
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    results = circuit.last_results
    if results is None:
        return {
            "status": "error",
            "error": "No simulation results — run an analysis first",
        }

    if results.get("analysis_type") != analysis_type:
        return {
            "status": "error",
            "error": (
                f"Expected {analysis_type} results but found "
                f"'{results.get('analysis_type')}'"
            ),
        }

    return (circuit, results)


_SOURCE_DC_RE = re.compile(
    r"^\s*(v\w+)\s+\S+\s+\S+\s+(?:dc\s+)?(\S+)",
    re.IGNORECASE | re.MULTILINE,
)


def _get_source_voltage(netlist: str, source_name: str) -> float | None:
    """Parse netlist text to extract DC voltage of a named voltage source."""
    for m in _SOURCE_DC_RE.finditer(netlist):
        if m.group(1).lower() == source_name.lower():
            try:
                return parse_spice_value(m.group(2))
            except ValueError:
                return None
    return None


# Mapping from user-facing spec names to (analysis_type, result_key)
_SPEC_MAP: dict[str, tuple[str, str]] = {
    "f_3dB_hz": ("AC Analysis", "f_3dB_hz"),
    "gain_dc_dB": ("AC Analysis", "gain_dc_dB"),
    "rolloff_dB_per_decade": ("AC Analysis", "rolloff_rate_dB_per_decade"),
    "rolloff_rate_dB_per_decade": ("AC Analysis", "rolloff_rate_dB_per_decade"),
    "peak_gain_dB": ("AC Analysis", "peak_gain_dB"),
    "phase_at_f3dB_deg": ("AC Analysis", "phase_at_f3dB_deg"),
    "steady_state_value": ("Transient Analysis", "steady_state_value"),
    "rise_time_10_90_s": ("Transient Analysis", "rise_time_10_90_s"),
    "overshoot_pct": ("Transient Analysis", "overshoot_pct"),
    "settling_time_1pct_s": ("Transient Analysis", "settling_time_1pct_s"),
}


def _extract_spec_value(results: dict, spec_name: str) -> float | None:
    """Look up a value from results using _SPEC_MAP with DC OP node fallback."""
    if spec_name in _SPEC_MAP:
        _, key = _SPEC_MAP[spec_name]
        return results.get(key)

    # DC OP node fallback: try direct lookup then case-insensitive
    nodes = results.get("nodes", {})
    if spec_name in nodes:
        return nodes[spec_name]
    for k, v in nodes.items():
        if k.lower() == spec_name.lower():
            return v
    return None


def _check_spec(actual: float | None, spec_def: dict) -> tuple[bool, dict]:
    """Evaluate a single spec against actual value.

    spec_def can contain:
      - {"target": N, "tolerance_pct": P}  — passes if within P% of N
      - {"min": N, "max": M}               — passes if min <= actual <= max
      - {"min": N}                          — passes if actual >= min
      - {"max": M}                          — passes if actual <= max
      - {"target": N}                       — passes if within 1% of N
    """
    detail: dict = {"actual": actual}

    if actual is None:
        detail["error"] = "Value not available in results"
        return False, detail

    if "target" in spec_def:
        target = spec_def["target"]
        tol_pct = spec_def.get("tolerance_pct", 1.0)
        margin = abs(target) * tol_pct / 100.0 if target != 0 else tol_pct / 100.0
        passed = abs(actual - target) <= margin
        detail.update({"target": target, "tolerance_pct": tol_pct, "margin": margin})
        return passed, detail

    passed = True
    if "min" in spec_def:
        detail["min"] = spec_def["min"]
        if actual < spec_def["min"]:
            passed = False
    if "max" in spec_def:
        detail["max"] = spec_def["max"]
        if actual > spec_def["max"]:
            passed = False

    return passed, detail


# ---------------------------------------------------------------------------
# Measurement tools
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(
        title="Measure Bandwidth",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def measure_bandwidth(circuit_id: str, threshold_db: float = -3.0) -> dict:
    """Measure the bandwidth (cutoff frequency) of an AC analysis result.

    Uses -3dB by default; specify threshold_db for custom cutoff levels.
    """
    if threshold_db >= 0:
        return {"status": "error", "error": "threshold_db must be negative"}

    check = _require_results(circuit_id, "AC Analysis")
    if isinstance(check, dict):
        return check
    circuit, results = check

    if threshold_db == -3.0:
        return {
            "status": "ok",
            "f_cutoff_hz": results.get("f_3dB_hz"),
            "rolloff_db_per_decade": results.get("rolloff_rate_dB_per_decade"),
            "threshold_db": threshold_db,
        }

    raw_path = circuit.output_dir / "circuit.raw"
    try:
        bw = read_ac_bandwidth(raw_path, threshold_db)
    except Exception as e:
        return safe_error_response(e, logger, "measure_bandwidth")

    return {
        "status": "ok",
        "f_cutoff_hz": bw["f_cutoff_hz"],
        "rolloff_db_per_decade": bw["rolloff_db_per_decade"],
        "threshold_db": threshold_db,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Measure Gain",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def measure_gain(circuit_id: str, frequency_hz: float) -> dict:
    """Measure gain and phase at a specific frequency from AC analysis results."""
    if frequency_hz <= 0:
        return {"status": "error", "error": "frequency_hz must be positive"}

    check = _require_results(circuit_id, "AC Analysis")
    if isinstance(check, dict):
        return check
    circuit, _results = check

    raw_path = circuit.output_dir / "circuit.raw"
    try:
        data = read_ac_at_frequency(raw_path, frequency_hz)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    return {
        "status": "ok",
        "frequency_hz": frequency_hz,
        "gain_db": data["gain_db"],
        "phase_deg": data["phase_deg"],
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Measure DC Voltage",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def measure_dc(circuit_id: str, node_name: str) -> dict:
    """Measure the DC voltage at a specific node from operating point results."""
    check = _require_results(circuit_id, "Operating Point")
    if isinstance(check, dict):
        return check
    _circuit, results = check

    nodes = results.get("nodes", {})

    # Direct lookup
    if node_name in nodes:
        return {"status": "ok", "node_name": node_name, "voltage_V": nodes[node_name]}

    # Case-insensitive fallback
    for k, v in nodes.items():
        if k.lower() == node_name.lower():
            return {"status": "ok", "node_name": k, "voltage_V": v}

    return {
        "status": "error",
        "error": f"Node '{node_name}' not found. Available nodes: {list(nodes.keys())}",
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Measure Transient Response",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def measure_transient(circuit_id: str) -> dict:
    """Extract key transient response metrics (rise time, settling time, overshoot)."""
    check = _require_results(circuit_id, "Transient Analysis")
    if isinstance(check, dict):
        return check
    _circuit, results = check

    rise_s = results.get("rise_time_10_90_s")
    settling_s = results.get("settling_time_1pct_s")

    return {
        "status": "ok",
        "rise_time_us": rise_s * 1e6 if rise_s is not None else None,
        "settling_time_us": settling_s * 1e6 if settling_s is not None else None,
        "overshoot_pct": results.get("overshoot_pct"),
        "steady_state_V": results.get("steady_state_value"),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Measure Power",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def measure_power(circuit_id: str) -> dict:
    """Measure power consumption from DC operating point results."""
    check = _require_results(circuit_id, "Operating Point")
    if isinstance(check, dict):
        return check
    circuit, results = check

    nodes = results.get("nodes", {})
    per_source: dict[str, dict] = {}
    total_power = 0.0

    for key, current in nodes.items():
        # ngspice branch current traces: i(v1) or v1#branch
        source_name = None
        if key.startswith("i(") and key.endswith(")"):
            source_name = key[2:-1]
        elif key.endswith("#branch"):
            source_name = key[: -len("#branch")]

        if source_name is None:
            continue

        voltage = _get_source_voltage(circuit.netlist, source_name)
        if voltage is None:
            continue

        # Power = -V * I (ngspice convention)
        power_w = -voltage * current
        per_source[source_name] = {
            "current_A": current,
            "voltage_V": voltage,
            "power_mW": power_w * 1e3,
        }
        total_power += power_w

    return {
        "status": "ok",
        "total_power_mW": total_power * 1e3,
        "per_source": per_source,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Compare Specs",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def compare_specs(circuit_id: str, specs: dict) -> dict:
    """Compare simulation results against design specifications.

    specs format: {"spec_name": {"target": N, "tolerance_pct": P}}
    or {"spec_name": {"min": N, "max": M}}.
    """
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    results = circuit.last_results
    if results is None:
        return {
            "status": "error",
            "error": "No simulation results — run an analysis first",
        }

    all_passed = True
    spec_results: dict[str, dict] = {}

    for spec_name, spec_def in specs.items():
        actual = _extract_spec_value(results, spec_name)
        passed, detail = _check_spec(actual, spec_def)
        detail["passed"] = passed
        spec_results[spec_name] = detail
        if not passed:
            all_passed = False

    return {
        "status": "ok",
        "all_passed": all_passed,
        "results": spec_results,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Calculate Components",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def calculate_components(topology_id: str, specs: dict) -> dict:
    """Calculate component values for a circuit topology from target specs."""
    try:
        result = _solve_components(topology_id, specs)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# auto_design — single-call design loop
# ---------------------------------------------------------------------------

# Maps compare_specs keys → solver parameter names
_SPEC_TO_SOLVER: dict[str, str] = {
    "f_3dB_hz": "f_cutoff_hz",
    "f_cutoff_hz": "f_cutoff_hz",
    "f_center_hz": "f_center_hz",
    "f_notch_hz": "f_notch_hz",
}

# Sensible defaults per simulation type
_DEFAULT_SIM_PARAMS: dict[str, dict] = {
    "ac": {"start_freq": 1, "stop_freq": 1e6, "points_per_decade": 20},
    "transient": {"stop_time": 10e-3, "step_time": 10e-6},
    "dc": {},
}


def _specs_to_solver_params(specs: dict) -> dict:
    """Extract target values from compare_specs format and map to solver keys.

    Input:  {"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}}
    Output: {"f_cutoff_hz": 1000}
    """
    solver_params: dict = {}
    for spec_key, spec_def in specs.items():
        if spec_key in _SPEC_TO_SOLVER:
            target = spec_def.get("target") if isinstance(spec_def, dict) else spec_def
            if target is not None:
                solver_params[_SPEC_TO_SOLVER[spec_key]] = target
    return solver_params


def _run_sim(circuit_id: str, sim_type: str, sim_params: dict | None) -> dict:
    """Merge user sim_params with defaults and dispatch to the right analysis."""
    defaults = _DEFAULT_SIM_PARAMS.get(sim_type, {})
    merged = {**defaults, **(sim_params or {})}

    if sim_type == "ac":
        return run_ac_analysis(circuit_id, **merged)
    elif sim_type == "transient":
        return run_transient(circuit_id, **merged)
    elif sim_type == "dc":
        return run_dc_op(circuit_id)
    else:
        return {"status": "error", "error": f"Unknown sim_type '{sim_type}'"}


def _collect_measurements(circuit_id: str, sim_type: str, specs: dict) -> dict:
    """Run relevant measure_* tools and collect results. Failures are silenced."""
    import contextlib

    measurements: dict = {}

    with contextlib.suppress(Exception):
        if sim_type == "ac":
            measurements["bandwidth"] = measure_bandwidth(circuit_id)
        elif sim_type == "transient":
            measurements["transient"] = measure_transient(circuit_id)
        elif sim_type == "dc":
            for spec_key in specs:
                # Node-voltage specs like "v(out)"
                if spec_key not in _SPEC_MAP:
                    with contextlib.suppress(Exception):
                        measurements[spec_key] = measure_dc(circuit_id, spec_key)
            with contextlib.suppress(Exception):
                measurements["power"] = measure_power(circuit_id)

    return measurements


@mcp.tool(
    annotations=ToolAnnotations(
        title="Auto Design",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def auto_design(
    template_id: str,
    specs: dict,
    sim_type: str = "ac",
    sim_params: dict | None = None,
) -> list:
    """Run the full design loop in one call: load template, simulate, and verify.

    *specs* uses compare_specs format:
        {"f_3dB_hz": {"target": 1000, "tolerance_pct": 5}}

    *sim_type* is one of "ac", "transient", or "dc".
    *sim_params* optionally overrides default simulation parameters.

    Returns accumulated results including circuit_id, simulation data,
    measurements, and spec comparison.  On failure at any step, returns
    partial results with an ``error`` key and ``failed_step``.

    When a schematic_url is present in the response, ALWAYS include it as a
    clickable link in your message to the user. Users cannot see inline images.
    The schematic_url in the response is a public internet-accessible URL, not a
    sandbox or localhost URL. Always share it directly with the user.
    """
    result: dict = {}

    # 1. Translate specs to solver format
    solver_specs = _specs_to_solver_params(specs)

    # 2. Load template (with solver specs if any mapped)
    load_args: dict = {"template_id": template_id}
    if solver_specs:
        load_args["specs"] = solver_specs
    loaded = load_template(**load_args)
    if loaded.get("status") != "ok":
        return _error_content({**result, **loaded, "failed_step": "load_template"})
    result["circuit_id"] = loaded["circuit_id"]
    result["netlist_preview"] = loaded.get("preview", [])
    result["calculated_values"] = loaded.get("calculated_values")
    result["solver_notes"] = loaded.get("solver_notes")

    circuit_id = loaded["circuit_id"]

    # 3. Validate netlist
    validation = validate_netlist(circuit_id)
    if validation.get("status") != "ok" or not validation.get("valid", False):
        return _error_content(
            {
                **result,
                "validation": validation,
                "failed_step": "validate_netlist",
                "status": "error",
                "error": "Netlist validation failed",
            }
        )

    # 4. Simulate
    sim_result = _run_sim(circuit_id, sim_type, sim_params)
    result["simulation"] = sim_result
    if sim_result.get("status") != "ok":
        return _error_content(
            {
                **result,
                "failed_step": "simulation",
                "status": "error",
                "error": sim_result.get("error", "Simulation failed"),
            }
        )

    # 5. Collect measurements
    result["measurements"] = _collect_measurements(circuit_id, sim_type, specs)

    # 6. Compare specs
    comparison = compare_specs(circuit_id, specs)
    result["comparison"] = comparison
    result["all_specs_passed"] = comparison.get("all_passed", False)

    # 7. Generate SVG schematic for cloud mode
    try:
        circuit = _manager.get(circuit_id)
        svg_file = safe_path(circuit.output_dir, "schematic.svg")
        _draw_schematic(circuit.netlist, svg_file, fmt="svg")
        result["svg_content"] = svg_file.read_text(encoding="utf-8")
    except Exception:
        pass  # schematic is best-effort; don't fail the design loop

    result["status"] = "ok"
    url = _schematic_url(circuit_id)
    if url:
        result["schematic_url"] = url
        result["_assistant_hint"] = (
            f"IMPORTANT: ALWAYS share this URL with the user in your response. Inline images are NOT visible to them in Claude.ai. The user can ONLY see the schematic if you include this link. This is a PUBLIC URL accessible from any browser, not a sandbox or internal URL: {url}"
        )

    if _http_transport:
        svg_for_image = result.pop("svg_content", None)
    else:
        svg_for_image = result.get("svg_content")
    blocks: list = [TextContent(type="text", text=json.dumps(result, default=str))]
    if _http_transport and url:
        blocks.append(
            TextContent(
                type="text",
                text=f"INSTRUCTION: The user cannot see inline images. You MUST include this schematic link in your response. This is a PUBLIC URL accessible from any browser, not a sandbox or internal URL: {url}",
            )
        )
    if svg_for_image and not _http_transport:
        blocks.append(_svg_to_image_content(svg_for_image, circuit_id=circuit_id))
    return blocks


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Model",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def create_model(
    component_type: str,
    name: str,
    parameters: dict | None = None,
) -> dict:
    """Generate a SPICE .lib model file from datasheet parameters.

    component_type: "opamp", "bjt", "mosfet", "diode"
    name: model name (e.g. "OPA2134")
    parameters: type-specific datasheet values; omitted keys use defaults.
    """
    try:
        model = generate_model(component_type, name, parameters)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    lib_path = _models.save(model)
    return {
        "status": "ok",
        "model_name": model.name,
        "component_type": model.component_type,
        "file_path": lib_path.name,
        "model_text": model.spice_text,
        "parameters_used": model.parameters,
        "notes": model.notes,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Models",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def list_models() -> dict:
    """Return all saved SPICE models from the model library."""
    return {
        "status": "ok",
        "models": _models.list_models(),
    }


def _build_analysis_params(
    start_freq: float | None,
    stop_freq: float | None,
    points_per_decade: int | None,
    stop_time: float | None,
    step_time: float | None,
) -> dict:
    """Collect non-None analysis parameters into a dict."""
    params: dict = {}
    if start_freq is not None:
        params["start_freq"] = start_freq
    if stop_freq is not None:
        params["stop_freq"] = stop_freq
    if points_per_decade is not None:
        params["points_per_decade"] = points_per_decade
    if stop_time is not None:
        params["stop_time"] = stop_time
    if step_time is not None:
        params["step_time"] = step_time
    return params


def _run_sensitivity_sweep(
    netlist: str,
    components: list,
    tolerances: dict | None,
    default_tol: float,
    analysis_cmd: str,
) -> tuple[list[tuple[str, int, dict]], int]:
    """Run +tol/-tol sweep for each component. Returns (sensitivity_runs, num_runs)."""
    n = len(components)
    sensitivity_runs: list[tuple[str, int, dict]] = []
    num_runs = 0
    for idx, comp in enumerate(components):
        for direction in (-1, 1):
            corner = tuple(0 if j != idx else direction for j in range(n))
            values = apply_corner(components, tolerances, default_tol, corner)
            modified = substitute_values(netlist, components, values)
            result = run_single_sim(modified, analysis_cmd)
            num_runs += 1
            if result is not None:
                sensitivity_runs.append((comp.ref, direction, result))
    return sensitivity_runs, num_runs


def _run_corner_analysis(
    netlist: str,
    components: list,
    tolerances: dict | None,
    default_tol: float,
    analysis_cmd: str,
    sensitivity: dict,
    max_sims: int = _MAX_WORST_CASE_SIMS,
) -> tuple[list[tuple[tuple[int, ...], dict]], int, str]:
    """Run exhaustive or sensitivity-guided corners.

    Returns (corner_results, num_runs, strategy).
    """
    n = len(components)
    corner_results: list[tuple[tuple[int, ...], dict]] = []
    num_runs = 0

    if n <= 8 and 2**n <= max_sims:
        strategy = "exhaustive"
        corners = generate_corners(n)
        for corner in corners:
            if num_runs >= max_sims:
                break
            values = apply_corner(components, tolerances, default_tol, corner)
            modified = substitute_values(netlist, components, values)
            result = run_single_sim(modified, analysis_cmd)
            num_runs += 1
            if result is not None:
                corner_results.append((corner, result))
    else:
        strategy = "sensitivity"
        predicted_corners: set[tuple[int, ...]] = set()
        for _metric_key, entries in sensitivity.items():
            max_corner = tuple(
                1
                if next(
                    (e["pct_per_pct"] for e in entries if e["component"] == c.ref), 0
                )
                >= 0
                else -1
                for c in components
            )
            min_corner = tuple(-d for d in max_corner)
            predicted_corners.add(max_corner)
            predicted_corners.add(min_corner)

        predicted_corners.add(tuple(1 for _ in range(n)))
        predicted_corners.add(tuple(-1 for _ in range(n)))

        max_predicted = min(100, max_sims)
        if len(predicted_corners) > max_predicted:
            predicted_corners = set(list(predicted_corners)[:max_predicted])

        for corner in predicted_corners:
            if num_runs >= max_sims:
                break
            values = apply_corner(components, tolerances, default_tol, corner)
            modified = substitute_values(netlist, components, values)
            result = run_single_sim(modified, analysis_cmd)
            num_runs += 1
            if result is not None:
                corner_results.append((corner, result))

    return corner_results, num_runs, strategy


def _solve_and_snap(
    template_id: str, specs: dict, params: dict | None, netlist: str
) -> tuple[str, dict[str, str] | None, list[str] | None, dict | None]:
    """Run solver, snap to E24, merge explicit params.

    Returns (netlist, calculated_values, solver_notes, remaining_params).
    remaining_params is None if params were already applied.
    Returns an error dict instead of the tuple if solver fails.
    """
    calculated_values: dict[str, str] | None = None
    solver_notes: list[str] | None = None

    try:
        solver_result = _solve_components(template_id, specs)
    except ValueError as exc:
        if "Unknown topology" in str(exc):
            solver_notes = [f"No solver for '{template_id}'; using template defaults."]
            return netlist, calculated_values, solver_notes, params
        raise

    solver_params: dict[str, str] = {}
    for name, raw_val in solver_result["components"].items():
        if raw_val in ("open", "0"):
            solver_params[name] = raw_val
        else:
            numeric = parse_spice_value(str(raw_val))
            snapped = snap_to_standard(numeric, "E24")
            solver_params[name] = format_engineering(snapped)
    calculated_values = dict(solver_params)
    solver_notes = solver_result.get("notes", [])

    if params:
        solver_params.update(params)

    netlist = substitute_params(netlist, solver_params)
    return netlist, calculated_values, solver_notes, None


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run Monte Carlo Analysis",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def run_monte_carlo(
    circuit_id: str,
    analysis_type: str,
    num_runs: int = 100,
    tolerances: dict | None = None,
    default_tolerance_pct: float = 5.0,
    seed: int | None = None,
    start_freq: float | None = None,
    stop_freq: float | None = None,
    points_per_decade: int | None = None,
    stop_time: float | None = None,
    step_time: float | None = None,
) -> dict:
    """Run Monte Carlo analysis under component tolerances.

    Randomly varies R/C/L component values within tolerance bands
    and runs multiple simulations to produce statistical results.

    analysis_type: "ac", "transient", or "dc_op"
    tolerances: map component ref or prefix (R/C/L) to tol %.
    """
    import random as _random  # nosec B311 — non-crypto PRNG for Monte Carlo simulation

    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    if not 1 <= num_runs <= _MAX_MONTE_CARLO_RUNS:
        return {
            "status": "error",
            "error": f"num_runs must be between 1 and {_MAX_MONTE_CARLO_RUNS}",
        }

    components = parse_component_values(circuit.netlist)
    if not components:
        return {
            "status": "error",
            "error": "No R/C/L components found in netlist",
        }

    analysis_params = _build_analysis_params(
        start_freq, stop_freq, points_per_decade, stop_time, step_time
    )

    try:
        analysis_cmd = build_analysis_cmd(analysis_type, **analysis_params)
    except (ValueError, KeyError) as e:
        return {"status": "error", "error": str(e)}

    from spicebridge.monte_carlo import _resolve_tolerance

    tolerances_applied = {
        c.ref: _resolve_tolerance(c.ref, tolerances, default_tolerance_pct)
        for c in components
    }

    rng = _random.Random(seed)
    all_results: list[dict] = []
    failures: list[dict] = []
    timed_out = False
    deadline = time.monotonic() + _MONTE_CARLO_TIMEOUT

    for i in range(num_runs):
        if time.monotonic() > deadline:
            timed_out = True
            break
        values = randomize_values(components, tolerances, default_tolerance_pct, rng)
        modified_netlist = substitute_values(circuit.netlist, components, values)
        result = run_single_sim(modified_netlist, analysis_cmd)
        if result is not None:
            all_results.append(result)
        else:
            failures.append({"run": i, "error": "Simulation failed"})

    statistics = compute_statistics(all_results)

    if not all_results:
        return {
            "status": "error",
            "error": f"All {len(failures)} Monte Carlo simulations failed",
            "num_runs": num_runs,
            "num_failed": len(failures),
            "analysis_type": analysis_type,
        }

    response: dict = {
        "status": "ok",
        "num_runs": num_runs,
        "num_completed": len(all_results) + len(failures),
        "num_successful": len(all_results),
        "num_failed": len(failures),
        "analysis_type": analysis_type,
        "tolerances_applied": tolerances_applied,
        "statistics": statistics,
        "failures": failures,
    }
    if timed_out:
        response["warning"] = (
            f"Monte Carlo timed out after {_MONTE_CARLO_TIMEOUT}s; "
            f"completed {len(all_results) + len(failures)} of {num_runs} runs"
        )
    return response


@mcp.tool(
    annotations=ToolAnnotations(
        title="Run Worst-Case Analysis",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def run_worst_case(
    circuit_id: str,
    analysis_type: str,
    tolerances: dict | None = None,
    default_tolerance_pct: float = 5.0,
    start_freq: float | None = None,
    stop_freq: float | None = None,
    points_per_decade: int | None = None,
    stop_time: float | None = None,
    step_time: float | None = None,
) -> dict:
    """Run worst-case analysis at tolerance extremes.

    Evaluates component sensitivity and deterministic corner
    combinations to find true worst-case performance bounds.

    analysis_type: "ac", "transient", or "dc_op"
    tolerances: map component ref or prefix (R/C/L) to tol %.
    """
    try:
        circuit = _manager.get(circuit_id)
    except KeyError as e:
        return {"status": "error", "error": str(e)}

    components = parse_component_values(circuit.netlist)
    if not components:
        return {
            "status": "error",
            "error": "No R/C/L components found in netlist",
        }

    if len(components) > _MAX_WORST_CASE_COMPONENTS:
        return {
            "status": "error",
            "error": (
                f"Too many components ({len(components)}) for worst-case analysis; "
                f"limit is {_MAX_WORST_CASE_COMPONENTS}"
            ),
        }

    analysis_params = _build_analysis_params(
        start_freq, stop_freq, points_per_decade, stop_time, step_time
    )

    try:
        analysis_cmd = build_analysis_cmd(analysis_type, **analysis_params)
    except (ValueError, KeyError) as e:
        return {"status": "error", "error": str(e)}

    from spicebridge.monte_carlo import _resolve_tolerance

    tolerances_applied = {
        c.ref: _resolve_tolerance(c.ref, tolerances, default_tolerance_pct)
        for c in components
    }

    # 1. Run nominal simulation
    nominal = run_single_sim(circuit.netlist, analysis_cmd)
    if nominal is None:
        return {"status": "error", "error": "Nominal simulation failed"}

    num_runs = 1  # nominal

    # 2. Sensitivity sweep
    sensitivity_runs, sweep_runs = _run_sensitivity_sweep(
        circuit.netlist, components, tolerances, default_tolerance_pct, analysis_cmd
    )
    num_runs += sweep_runs

    sensitivity = compute_sensitivity(
        nominal, components, sensitivity_runs, tolerances, default_tolerance_pct
    )

    # 3. Corner analysis (remaining budget)
    remaining_budget = max(0, _MAX_WORST_CASE_SIMS - num_runs)
    corner_results, corner_runs, strategy = _run_corner_analysis(
        circuit.netlist,
        components,
        tolerances,
        default_tolerance_pct,
        analysis_cmd,
        sensitivity,
        max_sims=remaining_budget,
    )
    num_runs += corner_runs

    worst_case = compute_worst_case(
        nominal, corner_results, components, tolerances, default_tolerance_pct
    )

    return {
        "status": "ok",
        "nominal": nominal,
        "worst_case": worst_case,
        "sensitivity": sensitivity,
        "strategy": strategy,
        "num_runs": num_runs,
        "tolerances_applied": tolerances_applied,
    }


def configure_for_remote() -> None:
    """Disable DNS rebinding protection for tunnel/remote access."""
    global _http_transport
    _http_transport = True
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )


if __name__ == "__main__":
    mcp.run()
