"""SPICEBridge — AI-powered circuit design through simulation."""

__version__ = "0.1.0"

from spicebridge.parser import parse_results
from spicebridge.simulator import run_simulation

__all__ = ["run_simulation", "parse_results"]
