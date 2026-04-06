# Cloudflare (Tunnels)

**Type:** External service (cloud networking)

## Role in SPICEBridge

Cloudflare tunnels provide the bridge between a locally-running SPICEBridge server and remote MCP clients (Claude.ai). The `cloudflared` CLI creates encrypted tunnels from the local machine to Cloudflare's edge network.

## FACTS

- Two tunnel modes: quick (temporary `trycloudflare.com` URL, no account) and named (permanent custom domain, requires account) (Source: `setup_wizard.py`).
- The setup wizard can auto-install cloudflared on macOS (Homebrew) and Debian/Ubuntu (APT) (Source: `setup_wizard.py`).
- Named tunnel config stored at `~/.cloudflared/config.yml` (Source: `setup_wizard.py`).
- Authentication via browser-based OAuth (`cloudflared tunnel login`) (Source: `setup_wizard.py`).
- Public instance URL: `https://spicebridge.clanker-lover.work/mcp` (Source: `docs/cloud-setup.md`).
- PRIVACY.md states: "No user data is forwarded to or stored by Cloudflare beyond what is necessary for TLS transport" (Source: `PRIVACY.md`).

## Related Pages

- [setup_wizard.py](../summaries/setup_wizard.md)
- [cloud-deployment](../concepts/cloud-deployment.md)
