"""Shared netlist utility functions."""

from __future__ import annotations

from spicebridge.constants import ANALYSIS_RE, END_RE


def prepare_netlist(netlist: str, analysis_line: str) -> str:
    """Strip existing analysis/.end commands and append new ones."""
    lines = []
    for line in netlist.splitlines():
        if ANALYSIS_RE.match(line):
            continue
        if END_RE.match(line):
            continue
        lines.append(line)
    lines.append(analysis_line)
    lines.append(".end")
    return "\n".join(lines) + "\n"
