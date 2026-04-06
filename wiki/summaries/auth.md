# auth.py

**Source:** `src/spicebridge/auth.py`

## Purpose

API key authentication middleware for HTTP transports. Enforces `Authorization: Bearer <key>` on every HTTP request when `SPICEBRIDGE_API_KEY` is set. Stdio transport is never authenticated.

## Public API

- **`ApiKeyMiddleware(app, api_key)`**: ASGI middleware class. Wraps a Starlette/FastMCP ASGI app.

## Behavior

- Only intercepts `scope["type"] == "http"` -- passes lifespan and websocket through.
- **Exempt paths**: `/schematics/*` (public image serving) and `/health` (monitoring).
- Returns 401 if no Authorization header or wrong scheme.
- Returns 403 if key mismatch (uses `hmac.compare_digest` for timing-safe comparison).

## Dependencies

`hmac`, `starlette.requests`, `starlette.responses`, `starlette.types`.

## Architecture Role

Security middleware layer. Applied in [__main__.py](__main__.md) when API key is configured for HTTP transports. See [security-model](../concepts/security-model.md).
