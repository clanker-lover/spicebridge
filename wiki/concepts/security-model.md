# Security Model

Cross-cutting concept appearing in: `sanitize.py`, `auth.py`, `server.py`, `setup_wizard.py`, `web_viewer.py`, `model_store.py`

## FACTS

SPICEBridge operates in a threat model where untrusted AI-generated netlists are executed against a real simulation engine. Multiple defense layers exist:

### Input Validation (Source: `sanitize.py`)
- **Directive allowlist**: Only 16 SPICE directives are permitted. Unknown directives are rejected.
- **`.include`/`.lib` blocking**: User-supplied netlists cannot include external files. Only model includes added by `_resolve_model_includes()` are allowed (using `_allow_includes=True` internally).
- **Backtick blocking**: Prevents shell execution in netlist text.
- **Continuation line reassembly**: Lines starting with `+` are joined to prevent directive splitting attacks.
- **Path traversal prevention**: `safe_path()` resolves paths and ensures they stay within base directories. Used by model store and KiCad export.
- **Error sanitization**: `sanitize_error()` strips internal filesystem paths from messages sent to clients.

### API Authentication (Source: `auth.py`)
- ASGI middleware enforces Bearer token auth on HTTP transports.
- Uses `hmac.compare_digest()` for timing-safe comparison.
- Schematic serving and health endpoints are exempt from auth.

### Web Viewer Security (Source: `web_viewer.py`)
- Token-based auth on all API/WS routes.
- Content Security Policy with script-src locked to inline script hash.
- WebSocket origin validation.
- Connection limits (50 WS clients, 1MB messages).

### Cloud Deployment (Source: `setup_wizard.py`)
- Strips dangerous environment variables from subprocess environment.
- Config files written with 0o600 permissions.
- Hostname/tunnel name validation with strict regexes.

## INFERENCES

The security model is defense-in-depth: input validation catches malicious netlists, auth prevents unauthorized access, and path safety prevents filesystem escapes. The `.include` blocking is particularly important since SPICE `.include` could read arbitrary files from the server.

## Related Pages

- [sanitize.py](../summaries/sanitize.md), [auth.py](../summaries/auth.md), [web_viewer.py](../summaries/web_viewer.md)
