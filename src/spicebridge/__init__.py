"""SPICEBridge — AI-powered circuit design through simulation."""

__version__ = "0.1.0"

from spicebridge.kicad_export import export_kicad_schematic
from spicebridge.model_generator import GeneratedModel, generate_model
from spicebridge.model_store import ModelStore
from spicebridge.parser import parse_results, read_ac_at_frequency, read_ac_bandwidth
from spicebridge.schematic import draw_schematic, parse_netlist
from spicebridge.simulator import run_simulation
from spicebridge.svg_renderer import render_svg
from spicebridge.template_manager import TemplateManager
from spicebridge.web_viewer import start_viewer

__all__ = [
    "run_simulation",
    "parse_results",
    "read_ac_at_frequency",
    "read_ac_bandwidth",
    "parse_netlist",
    "draw_schematic",
    "export_kicad_schematic",
    "TemplateManager",
    "generate_model",
    "GeneratedModel",
    "ModelStore",
    "render_svg",
    "start_viewer",
]
