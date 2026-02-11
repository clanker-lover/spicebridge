# Cloud Setup — SPICEBridge MCP Server

Run SPICEBridge as an HTTP server with a Cloudflare tunnel so that cloud MCP clients (Claude.ai, remote IDEs, etc.) can connect.

## Prerequisites

- Python 3.10+ with SPICEBridge installed in a virtualenv (`.venv`)
- ngspice installed (`sudo apt install ngspice`)
- `cloudflared` CLI (for tunnel access — see below)

## Installing cloudflared

### Debian / Ubuntu (apt)

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install cloudflared
```

### Binary download

Download the latest release from the [Cloudflare downloads page](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and place it on your `PATH`.

## Quick start

The startup script handles everything — server, tunnel, and cleanup:

```bash
./scripts/start_cloud.sh
```

It will print a `*.trycloudflare.com` URL. Use that URL in your MCP client config.

### Custom port

```bash
PORT=9000 ./scripts/start_cloud.sh
```

## Manual start

If you prefer to run the components separately:

### 1. Start the MCP server

```bash
# SSE transport (recommended for cloud)
python -m spicebridge --transport sse --port 8000

# Or streamable-http
python -m spicebridge --transport streamable-http --port 8000
```

### 2. Start the tunnel

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

The quick tunnel requires no Cloudflare account. `cloudflared` will print a temporary public URL.

## Connecting clients

### Claude.ai / Claude desktop

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "spicebridge": {
      "url": "https://<YOUR-TUNNEL-URL>/sse"
    }
  }
}
```

### Claude Code (local — unchanged)

Local usage still works via stdio. The existing `.mcp.json` is unchanged:

```json
{
  "mcpServers": {
    "spicebridge": {
      "command": ".venv/bin/python",
      "args": ["-m", "spicebridge.server"]
    }
  }
}
```

## Security notes

- Quick tunnels generate a random, temporary URL — it changes each restart.
- The server binds to `127.0.0.1` by default (localhost only). Cloudflare tunnel handles external access.
- DNS rebinding protection is automatically disabled for non-stdio transports so that tunnel traffic with `*.trycloudflare.com` Host headers is accepted.
- No authentication is built in. Anyone with the tunnel URL can use the tools. For production use, consider Cloudflare Access or a reverse proxy with auth.

## Troubleshooting

**Server won't start**
- Check that `.venv/bin/python` exists: `ls .venv/bin/python`
- Check that the port is free: `lsof -i :8000`
- Try running manually: `python -m spicebridge --transport sse`

**Tunnel URL not working**
- Verify local server is running: `curl http://127.0.0.1:8000/sse`
- Check `cloudflared` output for errors
- Quick tunnels can be slow to initialize — wait 5-10 seconds

**"DNS rebinding" errors**
- This should be handled automatically. If you see this error, ensure you're using `python -m spicebridge` (not `python -m spicebridge.server`) for HTTP transports.

**MCP client can't connect**
- Ensure the URL ends with `/sse` for SSE transport
- Check that the tunnel is still running (they time out after inactivity)
