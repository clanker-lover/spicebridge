# __main__.py

**Source:** `src/spicebridge/__main__.py`

## Purpose

CLI entry point for the SPICEBridge MCP server. Handles argument parsing, transport selection, and server startup. Also dispatches the `setup-cloud` subcommand to the setup wizard.

## Public API

- **`main()`**: Top-level entry point, registered as the `spicebridge` console script in `pyproject.toml`.

## Key Behavior

1. If `sys.argv[1] == "setup-cloud"`, delegates to `setup_wizard.run_wizard()` immediately.
2. Otherwise, parses `--transport` (stdio/sse/streamable-http), `--host`, `--port` arguments.
3. Sets `FASTMCP_HOST` and `FASTMCP_PORT` environment variables before importing `server` so pydantic-settings picks them up.
4. Registers SIGTERM and atexit handlers for clean metrics shutdown.
5. Calls `configure_for_remote()` for non-stdio transports.
6. If `SPICEBRIDGE_API_KEY` is set and transport is HTTP, wraps the ASGI app with `ApiKeyMiddleware` and runs via uvicorn.
7. Otherwise runs the MCP server directly via `mcp.run()`.

## Dependencies

`spicebridge.server` (lazy import after env setup), `spicebridge.auth.ApiKeyMiddleware`, `spicebridge.setup_wizard`, `argparse`, `uvicorn`, `anyio`.

## Architecture Role

Thin CLI shell. All server logic lives in [server.py](server.md). Authentication logic in [auth.py](auth.md). Cloud setup in [setup_wizard.py](setup_wizard.md).
