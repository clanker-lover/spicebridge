# Dependencies

External libraries and tools referenced across the SPICEBridge codebase.

## Runtime Dependencies

| Package | Version | Role | Referenced In |
|---|---|---|---|
| **ngspice** | system install | SPICE circuit simulator. The actual simulation engine. Must be on PATH. | `simulator.py`, `server.py`, `setup_wizard.py` |
| **spicelib** | >=1.4.0,<2 | Python bindings for ngspice. Provides `RawRead` for parsing `.raw` files and `NGspiceSimulator` for running sims. | `simulator.py`, `parser.py` |
| **numpy** | >=1.24,<3 | Numerical computation. Array operations for signal analysis, interpolation, statistics. | `parser.py`, `monte_carlo.py` |
| **mcp[cli]** | >=1.25,<2 | Model Context Protocol framework. Provides `FastMCP` server class, `ToolAnnotations`, `ImageContent`, `TextContent`. | `server.py`, `__main__.py` |
| **schemdraw** | >=0.19,<1 | Schematic diagram library. Renders circuit schematics as PNG/SVG using electronic component elements. | `schematic.py` |
| **aiohttp** | >=3.10.11,<4 | Async HTTP framework. Powers the web viewer server with REST API and WebSocket support. | `web_viewer.py` |
| **cairosvg** | >=2.7,<3 | SVG to PNG conversion. Used for schematic image generation and favicon rendering. | `server.py` |
| **psutil** | >=5.9,<7 | System metrics collection. CPU, RAM, disk usage for health endpoint. Optional -- degrades gracefully if missing. | `metrics.py` |
| **starlette** | (transitive via mcp) | ASGI framework. Provides Request, Response, middleware types for auth and HTTP routes. | `auth.py`, `server.py` |
| **uvicorn** | (transitive via mcp) | ASGI server. Used when API key auth is enabled for HTTP transports. | `__main__.py` |

## Dev Dependencies

| Package | Role |
|---|---|
| **ruff** | Linter/formatter (line-length 88, Python 3.10 target) |
| **pytest** | Test runner |
| **pytest-asyncio** | Async test support (mode: auto) |
| **pytest-aiohttp** | aiohttp test client support |

## System Requirements

- Python 3.10+
- ngspice installed and on PATH (`sudo apt install ngspice`)

## Related Pages

- [pyproject.toml](../summaries/pyproject.md) -- version constraints and build config
