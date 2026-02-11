"""CLI entry point for SPICEBridge MCP server.

Usage:
    python -m spicebridge                        # stdio (default)
    python -m spicebridge --transport sse         # HTTP + SSE on port 8000
    python -m spicebridge --transport streamable-http --port 9000
"""

from __future__ import annotations

import argparse
import logging
import os

logger = logging.getLogger(__name__)


def _run_with_auth(mcp, transport: str, host: str, port: int, api_key: str) -> None:
    """Start the MCP server with API key middleware via uvicorn."""
    import anyio
    import uvicorn

    from spicebridge.auth import ApiKeyMiddleware

    app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
    app = ApiKeyMiddleware(app, api_key)

    log_level = getattr(mcp.settings, "log_level", "info")

    async def _serve() -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level,
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="spicebridge",
        description="SPICEBridge MCP server for AI-powered circuit design",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.environ.get("SPICEBRIDGE_TRANSPORT", "stdio"),
        help="MCP transport type (default: stdio, or SPICEBRIDGE_TRANSPORT env)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
        help="Host to bind to for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FASTMCP_PORT", "8000")),
        help="Port for HTTP transports (default: 8000)",
    )
    args = parser.parse_args()

    # Set env vars before importing server so pydantic-settings picks them up
    os.environ["FASTMCP_HOST"] = args.host
    os.environ["FASTMCP_PORT"] = str(args.port)

    from spicebridge.server import configure_for_remote, mcp

    if args.transport != "stdio":
        configure_for_remote()

    api_key = os.environ.get("SPICEBRIDGE_API_KEY", "")

    if api_key and args.transport != "stdio":
        logger.info("API key authentication enabled for %s transport", args.transport)
        _run_with_auth(mcp, args.transport, args.host, args.port, api_key)
    else:
        if not api_key and args.transport != "stdio":
            logger.warning(
                "No API key configured — MCP server is unauthenticated. "
                "Set SPICEBRIDGE_API_KEY to enable authentication."
            )
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
