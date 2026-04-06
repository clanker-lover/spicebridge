# MCP Tool Architecture

Cross-cutting concept appearing in: `server.py`, `__main__.py`, `auth.py`, `pyproject.toml`, `prompts/*.md`, `SKILL.md`

## FACTS

SPICEBridge exposes 28 tools via the Model Context Protocol (MCP) using the FastMCP framework from the `mcp` package (Source: `server.py`). The server supports three transports: `stdio` (default, for local Claude Code), `sse` (HTTP + Server-Sent Events), and `streamable-http` (HTTP streaming) (Source: `__main__.py`).

Every tool function is decorated with `@mcp.tool(annotations=ToolAnnotations(...))` providing metadata hints: `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` (Source: `server.py`). Every tool is also wrapped by `@_monitored` for metrics and rate limiting.

The server instantiates four singletons at module level: `_manager` (CircuitManager), `_templates` (TemplateManager), `_models` (ModelStore), `_schematic_cache` (SchematicCache) (Source: `server.py`).

## INFERENCES

The tool annotations suggest the MCP framework uses these hints for client-side caching or retry decisions. The `idempotentHint=True` on simulation tools implies re-running with the same inputs produces equivalent results (the netlist is deterministic).

The `_monitored` decorator pattern creates a uniform instrumentation layer without polluting individual tool implementations -- a clean separation of cross-cutting concerns.

## OPEN QUESTIONS

- How does MCP handle tool discovery? The FastMCP framework presumably auto-generates the tool schema from Python function signatures and docstrings.
- What happens if the server is run in stdio mode with an API key set? The code silently ignores the key for stdio.

## Related Pages

- [server.py](../summaries/server.md) -- tool implementations
- [auth.py](../summaries/auth.md) -- HTTP authentication
- [observability](observability.md) -- metrics and monitoring
- [security-model](security-model.md) -- input validation
