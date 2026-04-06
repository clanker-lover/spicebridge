# pyproject.toml

**Source:** `pyproject.toml`

## Purpose

Python package configuration. Defines build system, dependencies, entry points, and tool settings.

## Key Facts

- **Name**: `spicebridge`, **Version**: `1.3.0`, **License**: GPL-3.0-or-later
- **Author**: Brandon
- **Python**: `>=3.10`
- **Build**: setuptools with setuptools-scm

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `spicelib` | `>=1.4.0,<2` | ngspice `.raw` file parsing |
| `numpy` | `>=1.24,<3` | Numerical computation |
| `mcp[cli]` | `>=1.25,<2` | MCP server framework (FastMCP) |
| `schemdraw` | `>=0.19,<1` | Static schematic rendering |
| `aiohttp` | `>=3.10.11,<4` | Web viewer HTTP server |
| `cairosvg` | `>=2.7,<3` | SVG to PNG conversion |
| `psutil` | `>=5.9,<7` | System metrics collection |

## Dev Dependencies

`ruff`, `pytest`, `pytest-asyncio`, `pytest-aiohttp`

## Entry Points

- `spicebridge` -> `spicebridge.__main__:main` (MCP server CLI)
- `spicebridge-viewer` -> `spicebridge.web_viewer:main` (standalone viewer CLI)

## Package Data

`templates/*.json` and `static/*` are included in the distribution.

## Ruff Config

Line length 88, target Python 3.10, select rules E/W/F/I/N/UP/B/SIM. Line length exemptions for `kicad_export.py`, `server.py`, `svg_renderer.py`, `web_viewer.py`.

## Architecture Role

Build/packaging configuration. See [entities/dependencies](../entities/dependencies.md).
