# web_viewer.py

**Source:** `src/spicebridge/web_viewer.py`

## Purpose

Browser-based interactive schematic viewer. Runs an aiohttp server in a daemon thread alongside the MCP server, sharing the same `CircuitManager` instance. Provides REST API and WebSocket push notifications.

## Public API

- **`start_viewer(manager, host="127.0.0.1", port=8080, open_browser=True)`**: Starts the viewer server (idempotent). Returns URL string.
- **`get_viewer_server()`**: Returns active `_ViewerServer` instance or None.

## HTTP Routes

- `GET /` -- Serves `viewer.html` with injected auth token.
- `GET /api/circuits` -- Lists all circuits.
- `GET /api/circuit/{id}` -- Returns circuit details (netlist, components, ports).
- `GET /api/circuit/{id}/svg` -- Returns interactive SVG schematic.
- `GET /api/circuit/{id}/results` -- Returns simulation results.
- `GET /ws` -- WebSocket endpoint for real-time updates.

## Security

- **Token auth middleware**: Bearer token required on all API/WS routes (index page exempted).
- **CSP headers**: Script-src locked to SHA-256 hash of inline script.
- **Origin validation**: WebSocket connections check Origin header.
- **Connection limits**: Max 50 WebSocket clients, max 1MB message size.

## Event System

`notify_change(event)` is thread-safe -- called from MCP thread when circuits are created/updated. A background `_broadcast_loop` polls every second and broadcasts events to all WS clients.

## Dependencies

`aiohttp`, `spicebridge.circuit_manager`, `spicebridge.schematic`, `spicebridge.svg_renderer`.

## Architecture Role

Real-time visualization layer. Called by [server.py](server.md) `open_viewer` tool and notified on circuit changes. See [visualization-pipeline](../concepts/visualization-pipeline.md).
