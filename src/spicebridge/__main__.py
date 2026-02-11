"""CLI entry point for SPICEBridge MCP server.

Usage:
    python -m spicebridge                        # stdio (default)
    python -m spicebridge --transport sse         # HTTP + SSE on port 8000
    python -m spicebridge --transport streamable-http --port 9000
"""

from __future__ import annotations

import argparse
import os


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

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
