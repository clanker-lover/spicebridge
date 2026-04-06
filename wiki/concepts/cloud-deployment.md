# Cloud Deployment

Cross-cutting concept appearing in: `setup_wizard.py`, `__main__.py`, `auth.py`, `server.py`, `docs/cloud-setup.md`

## FACTS

SPICEBridge supports cloud deployment via Cloudflare tunnels, enabling remote MCP clients (Claude.ai) to connect (Source: `docs/cloud-setup.md`, `setup_wizard.py`):

### Deployment Modes

**Quick tunnel**: `spicebridge setup-cloud --quick`. No Cloudflare account needed. Gets a temporary `trycloudflare.com` URL. URL changes on every restart. Auto-generates API key.

**Named tunnel**: `spicebridge setup-cloud`. Requires Cloudflare account. Creates a permanent custom domain (e.g., `spicebridge.example.com`). Generates `~/.cloudflared/config.yml`. DNS routing configured automatically.

**Manual**: `spicebridge --transport streamable-http --port 8000` + separate `cloudflared tunnel run`.

### Public Instance
A permanent public URL exists at `https://spicebridge.clanker-lover.work/mcp` (Source: `docs/cloud-setup.md`).

### Remote Mode Adjustments
`configure_for_remote()` in server.py (Source: `server.py`):
- Sets `_http_transport = True`.
- Enables RPM rate limiting.
- Strips SVG content from schematic responses (sends URL instead).
- Strips filepath from responses.

### Environment Variables
- `SPICEBRIDGE_API_KEY`: Enables Bearer token auth.
- `SPICEBRIDGE_HEALTH_TOKEN`: Enables health endpoint.
- `SPICEBRIDGE_BASE_URL`: Base URL for schematic serving.
- `SPICEBRIDGE_TRANSPORT`: Default transport mode.
- `SPICEBRIDGE_MAX_RPM`: Rate limit (default 60).
- `SPICEBRIDGE_MAX_CONCURRENT_SIMS`: Simulation concurrency (default 3).
- `SPICEBRIDGE_MAX_SIM_QUEUE`: Max queued simulations (default 5).

## Related Pages

- [setup_wizard.py](../summaries/setup_wizard.md), [auth.py](../summaries/auth.md)
- [security-model](security-model.md), [observability](observability.md)
