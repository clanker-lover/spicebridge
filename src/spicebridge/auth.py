"""API key authentication middleware for SPICEBridge MCP server.

When ``SPICEBRIDGE_API_KEY`` is set and the server uses an HTTP transport,
this ASGI middleware requires ``Authorization: Bearer <key>`` on every
HTTP request.  Stdio transport is never authenticated.
"""

from __future__ import annotations

import hmac
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class ApiKeyMiddleware:
    """ASGI middleware that enforces Bearer-token API key authentication."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self._api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only intercept HTTP requests — pass lifespan and websocket through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        auth_header = request.headers.get("authorization", "")

        if not auth_header:
            response = JSONResponse(
                {"error": "Authorization header required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        if not auth_header.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Authorization header must use Bearer scheme"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        provided_key = auth_header[7:]
        if not provided_key or not hmac.compare_digest(provided_key, self._api_key):
            response = JSONResponse(
                {"error": "Invalid API key"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
