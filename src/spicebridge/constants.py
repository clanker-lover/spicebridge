"""Shared constants used across SPICEBridge modules."""

from __future__ import annotations

import re

# Number of nodes per SPICE component letter (superset used by composer + schematic)
COMPONENT_NODE_COUNTS: dict[str, int] = {
    "R": 2,
    "C": 2,
    "L": 2,
    "V": 2,
    "I": 2,
    "D": 2,
    "Q": 3,
    "J": 3,
    "M": 4,
    "E": 4,
    "G": 4,
    "F": 2,
    "H": 2,
    "B": 2,
}

# Patterns for analysis commands to strip (case-insensitive)
ANALYSIS_RE = re.compile(r"^\s*\.(ac|tran|op|dc)\b", re.IGNORECASE)
END_RE = re.compile(r"^\s*\.end\s*$", re.IGNORECASE)
