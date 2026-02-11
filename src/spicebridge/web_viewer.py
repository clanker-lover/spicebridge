"""Browser-based interactive schematic viewer for SPICEBridge.

Runs an aiohttp server in a daemon thread alongside the MCP server,
sharing the same CircuitManager instance.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import importlib.resources  # nosemgrep: python37-compatibility-importlib2
import json
import logging
import secrets
import threading
import webbrowser
from typing import Any

from aiohttp import web

from spicebridge.circuit_manager import CircuitManager
from spicebridge.schematic import parse_netlist
from spicebridge.svg_renderer import render_svg

logger = logging.getLogger(__name__)

_MAX_WS_CLIENTS = 50
_MAX_EVENT_LOG = 1000


@web.middleware
async def security_headers(request: web.Request, handler):
    """Add security headers to all HTTP responses."""
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src 'self' data:"
    )
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _make_token_auth_middleware(auth_token: str):
    """Return an aiohttp middleware that checks a bearer token on API/WS routes."""

    @web.middleware
    async def token_auth(request: web.Request, handler):
        # Skip auth for the HTML index page
        if request.path == "/":
            return await handler(request)

        # Check query parameter first, then Authorization header
        token = request.query.get("token")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        if not token or not hmac.compare_digest(token, auth_token):
            raise web.HTTPUnauthorized(
                text="Valid authentication token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await handler(request)

    return token_auth


# Module-level singleton
_server: _ViewerServer | None = None
_lock = threading.Lock()


class _ViewerServer:
    """Encapsulates the aiohttp web application and its lifecycle."""

    def __init__(self, manager: CircuitManager, host: str, port: int) -> None:
        self.manager = manager
        self.host = host
        self.port = port
        self._auth_token: str = secrets.token_urlsafe(32)
        self._ws_clients: list[web.WebSocketResponse] = []
        self._event_log: list[dict] = []
        self._event_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _build_app(self) -> web.Application:
        token_auth = _make_token_auth_middleware(self._auth_token)
        app = web.Application(middlewares=[security_headers, token_auth])
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/circuits", self._handle_list_circuits)
        app.router.add_get("/api/circuit/{id}", self._handle_get_circuit)
        app.router.add_get("/api/circuit/{id}/svg", self._handle_get_svg)
        app.router.add_get("/api/circuit/{id}/results", self._handle_get_results)
        app.router.add_get("/ws", self._handle_ws)
        return app

    async def _handle_index(self, _request: web.Request) -> web.Response:
        ref = importlib.resources.files("spicebridge.static").joinpath("viewer.html")
        html = ref.read_text(encoding="utf-8")
        # Inject auth token so JS can authenticate API/WS requests
        token_script = (
            f'<script>window.__SPICEBRIDGE_TOKEN = "{self._auth_token}";</script>'
        )
        html = html.replace("</head>", f"{token_script}\n</head>")
        return web.Response(text=html, content_type="text/html")

    async def _handle_list_circuits(self, _request: web.Request) -> web.Response:
        circuits = self.manager.list_all()
        return web.json_response(circuits)

    async def _handle_get_circuit(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            state = self.manager.get(cid)
        except KeyError:
            raise web.HTTPNotFound(text=f"Circuit '{cid}' not found") from None
        components = parse_netlist(state.netlist)
        comp_data = [
            {
                "ref": c.ref,
                "comp_type": c.comp_type,
                "nodes": c.nodes,
                "value": c.value,
            }
            for c in components
        ]
        ports = self.manager.get_ports(cid)
        return web.json_response(
            {
                "circuit_id": cid,
                "netlist": state.netlist,
                "components": comp_data,
                "ports": ports,
                "has_results": state.last_results is not None,
            }
        )

    async def _handle_get_svg(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            state = self.manager.get(cid)
        except KeyError:
            raise web.HTTPNotFound(text=f"Circuit '{cid}' not found") from None
        svg_str = render_svg(state.netlist, results=state.last_results)
        return web.Response(text=svg_str, content_type="image/svg+xml")

    async def _handle_get_results(self, request: web.Request) -> web.Response:
        cid = request.match_info["id"]
        try:
            state = self.manager.get(cid)
        except KeyError:
            raise web.HTTPNotFound(text=f"Circuit '{cid}' not found") from None
        return web.json_response({"results": state.last_results})

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        # Connection limit
        if len(self._ws_clients) >= _MAX_WS_CLIENTS:
            raise web.HTTPServiceUnavailable(text="Too many WebSocket connections")

        # Origin validation
        origin = request.headers.get("Origin")
        if origin:
            from urllib.parse import urlparse

            parsed = urlparse(origin)
            expected_host = request.host.split(":")[0]
            if parsed.hostname and parsed.hostname not in (
                expected_host,
                "localhost",
                "127.0.0.1",
            ):
                raise web.HTTPForbidden(text="Invalid WebSocket origin")

        ws_resp = web.WebSocketResponse()
        await ws_resp.prepare(request)
        self._ws_clients.append(ws_resp)
        try:
            async for _msg in ws_resp:
                pass  # Client messages are ignored
        finally:
            self._ws_clients.remove(ws_resp)
        return ws_resp

    # ------------------------------------------------------------------
    # Event notification (thread-safe, called from MCP thread)
    # ------------------------------------------------------------------

    def notify_change(self, event: dict[str, Any]) -> None:
        """Record an event for broadcast. Safe to call from any thread."""
        with self._event_lock:
            if len(self._event_log) >= _MAX_EVENT_LOG:
                self._event_log = self._event_log[-(_MAX_EVENT_LOG // 2) :]
            self._event_log.append(event)

    async def _broadcast_loop(self) -> None:
        """Poll event log every second and broadcast to WS clients."""
        while True:
            await asyncio.sleep(1)
            events: list[dict] = []
            with self._event_lock:
                if self._event_log:
                    events = list(self._event_log)
                    self._event_log.clear()
            for event in events:
                payload = json.dumps(event)
                closed: list[web.WebSocketResponse] = []
                for ws_client in self._ws_clients:
                    try:
                        await ws_client.send_str(payload)
                    except Exception:
                        closed.append(ws_client)
                for c in closed:
                    if c in self._ws_clients:
                        self._ws_clients.remove(c)

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start_in_thread(self) -> None:
        """Spawn a daemon thread running the aiohttp server."""
        if self._thread is not None and self._thread.is_alive():
            return

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            app = self._build_app()
            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, self.host, self.port)
            loop.run_until_complete(site.start())
            loop.create_task(self._broadcast_loop())
            logger.info("Viewer running at %s", self.url)
            loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def start_viewer(
    manager: CircuitManager,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
) -> str:
    """Start the viewer server (idempotent). Returns the URL."""
    global _server
    with _lock:
        if _server is None:
            _server = _ViewerServer(manager, host, port)
            _server.start_in_thread()
            # Give the server a moment to bind
            import time

            time.sleep(0.3)
            if open_browser:
                webbrowser.open(_server.url)
        return _server.url


def get_viewer_server() -> _ViewerServer | None:
    """Return the active viewer server, or None if not started."""
    return _server


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for ``spicebridge-viewer``."""
    parser = argparse.ArgumentParser(description="SPICEBridge interactive viewer")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Port number")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically",
    )
    args = parser.parse_args()

    manager = CircuitManager()
    url = start_viewer(
        manager, host=args.host, port=args.port, open_browser=not args.no_browser
    )
    print(f"SPICEBridge Viewer running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        # Keep main thread alive
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down.")
