# Model Context Protocol (MCP)

**Type:** Protocol / Framework

## Role in SPICEBridge

MCP is the communication protocol that connects SPICEBridge to AI clients (Claude, local models). The `mcp` Python package provides `FastMCP`, a framework for building MCP-compliant tool servers.

## FACTS

- SPICEBridge uses `mcp[cli]` version >=1.25,<2 (Source: `pyproject.toml`).
- Server is instantiated as `FastMCP("SPICEBridge", instructions=...)` (Source: `server.py`).
- Tools are registered via `@mcp.tool(annotations=ToolAnnotations(...))` decorator (Source: `server.py`).
- Custom HTTP routes registered via `@mcp.custom_route()` (Source: `server.py`).
- Three transports supported: `stdio` (default), `sse`, `streamable-http` (Source: `__main__.py`).
- `mcp.run(transport=...)` handles the event loop and protocol negotiation (Source: `__main__.py`).
- For authenticated HTTP, the ASGI app is extracted via `mcp.sse_app()` or `mcp.streamable_http_app()` and wrapped with middleware (Source: `__main__.py`).
- Tool return types: `dict` for most tools, `list[TextContent | ImageContent]` for `draw_schematic` and `auto_design` (Source: `server.py`).

## Related Pages

- [server.py](../summaries/server.md), [__main__.py](../summaries/__main__.md)
- [mcp-tool-architecture](../concepts/mcp-tool-architecture.md)
- [dependencies](dependencies.md)
