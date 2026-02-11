"""SPICEBridge — AI-powered circuit design through simulation."""

__version__ = "0.1.0"

from spicebridge.parser import parse_results, read_ac_at_frequency, read_ac_bandwidth
from spicebridge.schematic import draw_schematic, parse_netlist
from spicebridge.simulator import run_simulation
from spicebridge.template_manager import TemplateManager

__all__ = [
    "run_simulation",
    "parse_results",
    "read_ac_at_frequency",
    "read_ac_bandwidth",
    "parse_netlist",
    "draw_schematic",
    "TemplateManager",
]
