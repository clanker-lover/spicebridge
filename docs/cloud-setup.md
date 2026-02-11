# Cloud Setup — SPICEBridge MCP Server

Run SPICEBridge as an HTTP server with a named Cloudflare tunnel so that cloud MCP clients (Claude.ai, remote IDEs, etc.) can connect via a permanent URL.

**Permanent URL:** `https://spicebridge.clanker-lover.work/mcp`

## Prerequisites

- Python 3.10+ with SPICEBridge installed in a virtualenv (`.venv`)
- ngspice installed (`sudo apt install ngspice`)
- `cloudflared` CLI authenticated with a named tunnel (see [Named tunnel setup](#named-tunnel-setup) below)

## Quick start

The startup script handles everything — server, tunnel, and cleanup:

```bash
./start_cloud.sh
```

The permanent URL is `https://spicebridge.clanker-lover.work/mcp`. Use that URL in your MCP client config.

### Custom port

```bash
PORT=9000 ./start_cloud.sh
```

> **Note:** When using a custom port, also update `~/.cloudflared/config.yml` to point the ingress `service` to the matching port.

## Named tunnel setup

SPICEBridge uses a **named Cloudflare tunnel** for a permanent, stable URL that doesn't change between restarts.

### 1. Install cloudflared

#### Debian / Ubuntu (apt)

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
  https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install cloudflared
```

#### Binary download

Download the latest release from the [Cloudflare downloads page](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and place it on your `PATH`.

### 2. Authenticate

```bash
cloudflared tunnel login
```

This opens a browser to authorize cloudflared with your Cloudflare account and saves a certificate to `~/.cloudflared/cert.pem`.

### 3. Create the tunnel

```bash
cloudflared tunnel create spicebridge
```

This creates a credentials file at `~/.cloudflared/<TUNNEL_ID>.json`.

### 4. Configure the tunnel

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: spicebridge
credentials-file: /home/<user>/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: spicebridge.<your-domain>
    service: http://localhost:8000
  - service: http_status:404
```

### 5. Route DNS

```bash
cloudflared tunnel route dns spicebridge spicebridge.<your-domain>
```

### 6. Validate

```bash
cloudflared tunnel ingress validate
```

## Manual start

If you prefer to run the components separately:

### 1. Start the MCP server

```bash
# Streamable HTTP transport (default for cloud)
python -m spicebridge --transport streamable-http --port 8000

# Or SSE transport
python -m spicebridge --transport sse --port 8000
```

### 2. Start the tunnel

```bash
cloudflared tunnel run spicebridge
```

## Connecting clients

### Claude.ai / Claude Desktop

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "spicebridge": {
      "url": "https://spicebridge.clanker-lover.work/mcp"
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

- The named tunnel provides a permanent URL tied to your Cloudflare domain.
- The server binds to `127.0.0.1` by default (localhost only). Cloudflare tunnel handles external access.
- DNS rebinding protection is automatically disabled for non-stdio transports so that tunnel traffic is accepted.
- No authentication is built in. Anyone with the tunnel URL can use the tools. For production use, consider [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) or a reverse proxy with auth.

## Troubleshooting

**Server won't start**
- Check that `.venv/bin/python` exists: `ls .venv/bin/python`
- Check that the port is free: `lsof -i :8000`
- Try running manually: `python -m spicebridge --transport streamable-http`

**Tunnel won't connect**
- Verify credentials: `ls ~/.cloudflared/*.json`
- Validate config: `cloudflared tunnel ingress validate`
- Check tunnel status: `cloudflared tunnel list`
- Verify local server is running: `curl http://127.0.0.1:8000/mcp`

**"DNS rebinding" errors**
- This should be handled automatically. If you see this error, ensure you're using `python -m spicebridge` (not `python -m spicebridge.server`) for HTTP transports.

**MCP client can't connect**
- Ensure the URL ends with `/mcp` for streamable-http transport or `/sse` for SSE transport
- Check that the tunnel is running: `cloudflared tunnel list`
